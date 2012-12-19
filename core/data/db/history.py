'''
Copyright 2009 Andres Riancho

This file is part of w3af, w3af.sourceforge.net .

w3af is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation version 2 of the License.

w3af is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with w3af; if not, write to the Free Software
Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

'''
from __future__ import with_statement

import os
import time
import threading

from shutil import rmtree
from errno import EEXIST

try:
    from cPickle import Pickler, Unpickler
except ImportError:
    from pickle import Pickler, Unpickler

try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO

import core.data.kb.config as cf
import core.data.kb.knowledge_base as kb
from core.controllers.exceptions import w3afException
from core.controllers.misc.temp_dir import get_temp_dir
from core.controllers.misc.FileLock import FileLock, FileLockRead
from core.data.db.db import DB
from core.data.db.db import WhereHelper
from core.data.fuzzer.utils import rand_alpha

class HistoryItem(object):
    '''Represents history item.'''

    _db = None
    _DATA_TABLE = 'data_table'
    _COLUMNS = [
        ('id', 'integer'), ('url', 'text'), ('code', 'integer'),
        ('tag', 'text'), ('mark', 'integer'), ('info', 'text'),
        ('time', 'float'), ('msg', 'text'), ('content_type', 'text'),
        ('charset', 'text'), ('method', 'text'), ('response_size', 'integer'),
        ('codef', 'integer'), ('alias', 'text'), ('has_qs', 'integer')
    ]
    _PRIMARY_KEY_COLUMNS = ('id',)
    _INDEX_COLUMNS = ('alias',)

    _EXTENSION = '.trace'

    id = None
    _request = None
    _response = None
    info = None
    mark = False
    tag = ''
    content_type = ''
    response_size = 0
    method = 'GET'
    msg = 'OK'
    code = 200
    time = 0.2

    history_lock = threading.RLock()

    def __init__(self):
        '''Construct object.'''
        with self.history_lock:
            if kb.kb.get('history', 'db') == []:
                # This means that it is the first time that w3af creates a
                # HistoryItem and we need to create some dirs and DBs
                self.init_structure()
            else:
                self._db = kb.kb.get('history', 'db')
                self._session_dir = kb.kb.get('history', 'session_dir')

    def get_response(self):
        resp = self._response
        if not resp and self.id:
            self._request, resp = self._loadFromFile(self.id)
            self._response = resp
        return resp

    def set_response(self, resp):
        self._response = resp

    response = property(get_response, set_response)

    def get_request(self):
        req = self._request
        if not req and self.id:
            req, self._response = self._loadFromFile(self.id)
            self._request = req
        return req

    def set_request(self, req):
        self._request = req

    request = property(get_request, set_request)
    
    def init_structure(self):
        '''Init history structure.'''
        session_name = cf.cf.get('session_name')
        if session_name is None:
            # This is the case of unittests where we "forget" to set the proper
            # cf variables. Because I don't want to set the session name in all
            # unittests, I do it here.
            session_name = 'unittest'

        db_name = os.path.join(get_temp_dir(), 'db_' + session_name)

        # Find one database file that does NOT exist
        for _ in xrange(100):
            newdb_name = db_name + '-' + rand_alpha(4)
            if not os.path.exists(newdb_name):
                db_name = newdb_name
                break

        self._db = DB(db_name)

        self._session_dir = os.path.join(get_temp_dir(),
                                         self._db.get_file_name() + '_traces')
        tablename = self.get_table_name()
        # Init tables
        self._db.create_table(tablename,
                              self.get_columns(),
                              self.get_primary_key_columns())
        self._db.create_index(tablename, self.get_index_columns())
        # Init dirs
        try:
            os.mkdir(self._session_dir)
        except OSError, oe:
            # [Errno EEXIST] File exists
            if oe.errno != EEXIST:
                msg = 'Unable to write to the user home directory "%s".'
                raise w3afException(msg % get_temp_dir())
        kb.kb.save('history', 'db', self._db)
        kb.kb.save('history', 'session_dir', self._session_dir)

    def find(self, searchData, result_limit=-1, orderData=[], full=False):
        '''Make complex search.
        search_data = {name: (value, operator), ...}
        orderData = [(name, direction)]
        '''
        if not self._db:
            raise w3afException('The database is not initialized yet.')
        result = []
        sql = 'SELECT * FROM ' + self._DATA_TABLE
        where = WhereHelper(searchData)
        sql += where.sql()
        orderby = ""
        #
        # TODO we need to move SQL code to parent class
        #
        for item in orderData:
            orderby += item[0] + " " + item[1] + ","
        orderby = orderby[:-1]

        if orderby:
            sql += " ORDER BY " + orderby

        sql += ' LIMIT ' + str(result_limit)
        try:
            for row in self._db.select(sql, where.values()):
                item = self.__class__()
                item._loadFromRow(row, full)
                result.append(item)
        except w3afException:
            raise w3afException(
                'You performed an invalid search. Please verify your syntax.')
        return result

    def _loadFromRow(self, row, full=True):
        '''Load data from row with all columns.'''
        self.id = row[0]
        self.url = row[1]
        self.code = row[2]
        self.tag = row[3]
        self.mark = bool(row[4])
        self.info = row[5]
        self.time = float(row[6])
        self.msg = row[7]
        self.content_type = row[8]
        self.charset = row[9]
        self.method = row[10]
        self.response_size = int(row[11])

    def _loadFromFile(self, id):

        fname = os.path.join(self._session_dir, str(id) + self._EXTENSION)
        #
        #    Due to some concurrency issues, we need to perform this check
        #    before we try to read the .trace file.
        #
        if not os.path.exists(fname):

            for _ in xrange(1 / 0.05):
                time.sleep(0.05)
                if os.path.exists(fname):
                    break
            else:
                msg = 'Timeout expecting trace file to be written "%s"' % fname
                raise IOError(msg)

        #
        #    Ok... the file exists, but it might still be being written
        #
        with FileLockRead(fname, timeout=1):
            rrfile = open(fname, 'rb')
            req, res = Unpickler(rrfile).load()
            rrfile.close()
            return (req, res)

    def delete(self, id=None):
        '''Delete data from DB by ID.'''
        if not self._db:
            raise w3afException('The database is not initialized yet.')
        if not id:
            id = self.id
        sql = 'DELETE FROM ' + self._DATA_TABLE + ' WHERE id = ? '
        self._db.execute(sql, (id,))
        # FIXME
        # don't forget about files!

    def load(self, id=None, full=True, retry=True):
        '''Load data from DB by ID.'''
        if not self._db:
            raise w3afException('The database is not initialized yet.')

        if not id:
            id = self.id

        sql = 'SELECT * FROM ' + self._DATA_TABLE + ' WHERE id = ? '
        try:
            row = self._db.select_one(sql, (id,))
        except Exception, e:
            msg = 'An unexpected error occurred while searching for id "%s".'\
                  ' Original exception: "%s".'
            raise w3afException(msg % (id, e))
        else:
            if row is not None:
                self._loadFromRow(row, full)
            else:
                # The request/response with 'id' == id is not in the DB!
                # Lets do some "error handling" and try again!

                if retry:
                    #    TODO:
                    #    According to sqlite3 documentation this db.commit()
                    #    might fix errors like
                    #    https://sourceforge.net/apps/trac/w3af/ticket/164352 ,
                    #    but it can degrade performance due to disk IO
                    #
                    self._db.commit()
                    self.load(id=id, full=full, retry=False)
                else:
                    # This is the second time load() is called and we end up here,
                    # raise an exception and finish our pain.
                    msg = ('An internal error occurred while searching for '
                           'id "%s", even after commit/retry' % id)
                    raise w3afException(msg)

        return True

    def read(self, id, full=True):
        '''Return item by ID.'''
        if not self._db:
            raise w3afException('The database is not initialized yet.')
        result_item = self.__class__()
        result_item.load(id, full)
        return result_item

    def save(self):
        '''Save object into DB.'''
        resp = self.response
        values = []
        values.append(resp.get_id())
        values.append(self.request.get_uri().url_string)
        values.append(resp.get_code())
        values.append(self.tag)
        values.append(int(self.mark))
        values.append(str(resp.info()))
        values.append(resp.get_wait_time())
        values.append(resp.get_msg())
        values.append(resp.content_type)
        ch = resp.charset
        values.append(ch)
        values.append(self.request.get_method())
        values.append(len(resp.body))
        code = int(resp.get_code()) / 100
        values.append(code)
        values.append(resp.get_alias())
        values.append(int(self.request.get_uri().has_query_string()))

        if not self.id:
            sql = ('INSERT INTO %s '
                   '(id, url, code, tag, mark, info, time, msg, content_type, '
                   'charset, method, response_size, codef, alias, has_qs) '
                   'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)' % self._DATA_TABLE)
            self._db.execute(sql, values)
            self.id = self.response.get_id()
        else:
            values.append(self.id)
            sql = ('UPDATE %s'
                   ' SET id = ?, url = ?, code = ?, tag = ?, mark = ?, info = ?, '
                   'time = ?, msg = ?, content_type = ?, charset = ?, '
                   'method = ?, response_size = ?, codef = ?, alias = ?, has_qs = ? '
                   ' WHERE id = ?' % self._DATA_TABLE)
            self._db.execute(sql, values)

        #
        # Save raw data to file
        #
        fname = os.path.join(self._session_dir,
                             str(self.response.id) + self._EXTENSION)

        with FileLock(fname, timeout=1):
            rrfile = open(fname, 'wb')
            p = Pickler(rrfile)
            p.dump((self.request, self.response))
            rrfile.close()
            return True

    def get_columns(self):
        return self._COLUMNS

    def get_table_name(self):
        return self._DATA_TABLE

    def get_primary_key_columns(self):
        return self._PRIMARY_KEY_COLUMNS

    def get_index_columns(self):
        return self._INDEX_COLUMNS

    def _updateField(self, name, value):
        '''Update custom field in DB.'''
        sql = 'UPDATE ' + self._DATA_TABLE
        sql += ' SET ' + name + ' = ? '
        sql += ' WHERE id = ?'
        self._db.execute(sql, (value, self.id))

    def update_tag(self, value, forceDb=False):
        '''Update tag.'''
        self.tag = value
        if forceDb:
            self._updateField('tag', value)

    def toggle_mark(self, forceDb=False):
        '''Toggle mark state.'''
        self.mark = not self.mark
        if forceDb:
            self._updateField('mark', int(self.mark))

    def clear(self):
        '''Clear history and delete all trace files.'''
        if not self._db:
            raise w3afException('The database is not initialized yet.')
        
        # Get the DB filename 
        db_filename = self._db.get_file_name()
        self._db.close()
        self._db = None
        
        # Delete files
        os.remove(db_filename)
        # It might be the case that another thread removes the session dir
        # at the same time as we, so we simply ignore errors here
        rmtree(self._session_dir, ignore_errors=True)
        
        kb.kb.save('history', 'db', [])
        
        return True
