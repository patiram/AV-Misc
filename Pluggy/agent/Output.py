#
# License:
#
#    Copyright (c) 2003-2006 ossim.net
#    Copyright (c) 2007-2011 AlienVault
#    All rights reserved.
#
#    This package is free software; you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation; version 2 dated June, 1991.
#    You may not use, modify or distribute this program under any other version
#    of the GNU General Public License.
#
#    This package is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this package; if not, write to the Free Software
#    Foundation, Inc., 51 Franklin St, Fifth Floor, Boston,
#    MA  02110-1301  USA
#
#
# On Debian GNU/Linux systems, the complete text of the GNU General
# Public License can be found in `/usr/share/common-licenses/GPL-2'.
#
# Otherwise you can read it here: http://www.gnu.org/licenses/gpl-2.0.txt
#

#
# GLOBAL IMPORTS
#
import os
import re
import string
import sys
import threading
import Queue
import sqlite3
import time
#
# LOCAL IMPORTS
#
from Config import Conf, CommandLineOptions
from Event import Event, EventIdm
from Exceptions import AgentCritical
from Logger import Logger

#
# GLOBAL VARIABLES
#
logger = Logger.logger

# You can use a global variable in other functions by declaring it as global in each function that modifies it
can_send = False
lock_send_events = threading.Lock()


SIZE_TO_WARNING = 5


PATH_TO_STORE_EVENTS = "/var/ossim/agent_events/"
class StoredEvent:
    '''
        Represents a stored Event. 
    '''
    def __init__(self, e, priority):
        self.__event = e
        self.__priority = priority
        self.__isSent = False

    def get_event(self):
        return self.__event


    def get_is_sent(self):
        return self.__isSent


    def set_event(self, value):
        self.__event = value


    def set_is_sent(self, value):
        self.__isSent = value


    def del_event(self):
        del self.__event


    def del_is_sent(self):
        del self.__isSent

    def get_priority(self):
        return self.__priority


    event = property(get_event, set_event, del_event, "event's docstring")
    isSend = property(get_is_sent, set_is_sent, del_is_sent, "isSend's docstring")


class storedEventSenderThread(threading.Thread):
    '''
        Thread to send stored events to the server.
    '''
    def __init__(self, queue, sendFunctionPtr, filename):
        '''
        Constructor:
        queue (Queue.Queue): Queue to receive event to be stored.
        sendFuntionPtr: Pointer to function used to send events.
        filename: Filename of the file where we store events. 
        '''
        threading.Thread.__init__(self)
        self.__keep_working = True
        self.__storeQueue = queue
        self.__eventList = []
#        self.__canSendEvents = sendEvents_event
        self.__filename = filename
        self.__sendFunction = sendFunctionPtr
        self.__dbConnection = None
        self.__dbCursor = None

    def createDB(self):
        self.__dbConnection = sqlite3.connect(self.__filename)
        self.__dbConnection.execute("VACUUM")
        self.__dbCursor = self.__dbConnection.cursor()
        self.__dbCursor.execute("CREATE TABLE IF NOT EXISTS stored_events(event_id INTEGER PRIMARY KEY,event TEXT,priority INTEGER NOT NULL,sent INTEGER NOT NULL);")
        #self.__dbCursor.execute("insert into stored_events values (NULL,\"texto\",0,0);")
        self.__dbConnection.commit()

    def openDBConn(self):
        self.__dbConnection = sqlite3.connect(self.__filename)
        self.__dbConnection.execute("VACUUM")
        self.__dbCursor = self.__dbConnection.cursor()
        self.__dbCursor.execute("CREATE TABLE IF NOT EXISTS stored_events(event_id INTEGER PRIMARY KEY,event TEXT,priority INTEGER NOT NULL,sent INTEGER NOT NULL);")
        self.__dbConnection.commit()
    def updateDB(self):
        for event in self.__eventList:
            issent = 0
            if event.get_is_sent():
                issent = 1
            query = "insert into stored_events values (NULL,'%s',%d,%d);" % (str(event.get_event()).strip(), event.get_priority(), issent)
            logger.debug("Query: %s" % query)            
            self.__dbCursor.execute(query)
        self.__dbConnection.commit()
        logger.info("Storing-- events in DB... %s " % (len(self.__eventList)))
        del self.__eventList[:]


    def setEventRead(self, event_id):
        query = "update stored_events set sent=1 where event_id=%d;" % event_id
        self.__dbCursor.execute(query)
        self.__dbConnection.commit()
    def deleteSentEvents(self):
        query = "delete from stored_events where sent='1';"
        self.__dbCursor.execute(query)
        self.__dbConnection.commit()
        #logger.info("Deleting sent events from temporal DB!")

    def run(self):
        '''
            Main thread function
        '''
        logger.info("Storing events locally....")
        global lock_send_events
        if not os.path.exists(PATH_TO_STORE_EVENTS):
            os.makedirs(PATH_TO_STORE_EVENTS)
        if not os.path.isfile(self.__filename):
            logger.info("Creating temporal events database... %s" % self.__filename)
            self.createDB()
        else:
            self.openDBConn()
        time_to_delete = 15
        tmp_time = 0
        n_stored_events = 0
        while self.__keep_working:
            if not self.__storeQueue.empty():
                #recieved event to store
                ev = self.__storeQueue.get_nowait()
                self.__eventList.append(ev)
                logger.info("THREAD_CACHE - Storing  events  %s " % (len(self.__eventList)))
                n_stored_events = n_stored_events + 1
                if n_stored_events >= 100:                    
                    self.updateDB()
                    n_stored_events = 0
            else:
                tmp_can_send = False
                lock_send_events.acquire()
                tmp_can_send = can_send
                lock_send_events.release()
                
                if tmp_can_send:                    
                    if n_stored_events > 0:
                        self.updateDB()
                        n_stored_events = 0
                    self.__dbCursor.execute("select * from stored_events where sent='0' limit 100;")
                    events_tmp = self.__dbCursor.fetchall()
                    nevents = len(events_tmp)
                    if nevents > 0:
                        #logger.info("Events to send.. %s" % nevents)
                        max_to_send = 100 #100 eps
                        events_sent = 0
                        for event in events_tmp:
                            if len(event) == 4:
                                id = event[0]
                                ev = "%s\n" % event[1]
                                prio = event[2]
                                sent = event[3]
                                logger.info("THREAD_CACHE - Sending  cached event -> %s " % str(ev))
                                self.__sendFunction(ev, prio)
                                self.setEventRead(id)
                                events_sent = events_sent + 1
                                if events_sent > max_to_send:
                                    break
                        self.deleteSentEvents()
#                tmp_time = tmp_time + 1
#                if tmp_time == time_to_delete:
#                    tmp_time = 0
#                    self.deleteSentEvents()
                time.sleep(1)

        logger.info("Stopping store thread...")
        self.__dbCursor.close()
        self.__dbConnection.close()


    def shutdown(self):
        self.__keep_working = False


class OutputPlugins:

    def _open_file(self, file):
        dir = file.rstrip(os.path.basename(file))

        if not os.path.isdir(dir):
            try:
                os.makedirs(dir, 0755)

            except OSError, e:
                raise AgentCritical("Error creating directory (%s): %s" % \
                    (dir, e))

        try:
            fd = open(file, 'a')

        except IOError, e:
            raise AgentCritical("Error opening file (%s): %s" % (file, e))

        return fd

    #
    # the following methods must be overriden in child classes
    #
    def event(self, e, priority=0):
        pass


    def shutdown(self):
        pass


    def plugin_state(self, msg):
        pass


class OutputPlain(OutputPlugins):

    def __init__(self, conf):
        self.conf = conf
        logger.info("Added Plain output")
        logger.debug("OutputPlain options: %s" % \
            (self.conf.hitems("output-plain")))
        self.plain = self._open_file(self.conf.get("output-plain", "file"))
        self.activated = True


    def event(self, e, priority=0, policy='all'):
        if self.activated:
            self.plain.write(str(e))
            self.plain.flush()


    def plugin_state(self, msg):
        if self.activated:
            self.plain.write(msg)
            self.plain.flush()


    def shutdown(self):
        logger.info("Closing Plain file..")
        self.plain.flush()
        self.plain.close()
        self.activated = False


class OutputServer(OutputPlugins):

    def __init__(self, conn, priority, sendEvents):
        logger.info("Added Server output (%s:%s)" % (conn.get_server_ip(), conn.get_server_port()))
        self.conn = conn
        self.ip = self.conn.get_server_ip()
        self.activated = True
        self.send_events = sendEvents
        self.__mypriority = priority
        self.options = CommandLineOptions().get_options()
        #self.__sendEvents_Event = threading.Event()
        self.__storeThread = None
        self.__storeQueue = Queue.Queue()
        self.__filename = PATH_TO_STORE_EVENTS + "%s.%s.db" % (conn.get_server_ip(), conn.get_server_port())
        logger.info("Path to store events: %s" % self.__filename)
        global can_send
        can_send = False
        self.__storeThread = storedEventSenderThread(self.__storeQueue, self.event , self.__filename)
        #self.__storeThread.start()
        self.__sendEvents = True
        

    def startCacheThread(self):
        self.__storeThread.start()


    def event(self, e, priority, policy='all'):

        if policy not in ['all', '']:
            logger.info("Filter by policy: %s" % policy)
            if self.ip not in policy.split(','):
                return
        global can_send
        global lock_send_events
        if self.activated and self.send_events and self.__mypriority <= priority:
            try:
                if not self.conn.get_is_alive():
                    self.__storeEvent(e, priority)
                    if self.__sendEvents:
                        logger.info("Send_Events disabled! - No server connection available")
                    self.__sendEvents = False
                    lock_send_events.acquire()
                    can_send = False                    
                    lock_send_events.release()
                else:
                    self.conn.send(str(e))
                    if not self.__sendEvents:
                        logger.info("Send_Events enabled! ")
                    self.__sendEvents = True
                    lock_send_events.acquire()
                    can_send = True   
                    lock_send_events.release()
                    
            except:
                if self.__sendEvents:
                    logger.info("Send_Events disabled! - No server connection available")
                self.__sendEvents = False
                lock_send_events.acquire()
                can_send = False
                lock_send_events.release()
                self.__storeEvent(e, priority)



    def __storeEvent(self, event, priority):
        stEvent = StoredEvent(event, priority)
        #logger.info("Storing event...:%s" % event)
        self.__storeQueue.put_nowait(stEvent)


    def plugin_state(self, msg):
        global can_send
        global lock_send_events
       
        if self.activated:
            try:
                if not self.conn.get_is_alive():
                    self.__sendEvents = False
                    lock_send_events.acquire()
                    can_send = False   
                    lock_send_events.release()
                else:                    
                    self.conn.send(msg)
                    if not self.__sendEvents:
                        logger.info("Send_Events enabled! ")
                    self.__sendEvents = True
                    lock_send_events.acquire()
                    can_send = True   
                    lock_send_events.release()
            except:
                if self.__sendEvents:
                    logger.info("Send_Events disabled! - No server connection available")
                self.__sendEvents = False
                lock_send_events.acquire()
                can_send = False   
                lock_send_events.release()
                

    def shutdown(self):
        self.conn.close()
        self.activated = False
        if self.__storeThread:
            self.__storeThread.shutdown()
            time.sleep(1)
            self.__storeThread.join()

class OutputCSV(OutputPlugins):

    def __init__(self, conf):

        self.conf = conf
        logger.info("Added CSV output")
        logger.debug("OutputCSV options: %s" % (self.conf.hitems("output-csv")))

        file = self.conf.get("output-csv", "file")
        first_creation = not os.path.isfile(file)
        self.csv = self._open_file(file)
        if first_creation:
            self.__write_csv_header()
        self.activated = True


    def __write_csv_header(self):

        header = ''

        for attr in Event.EVENT_ATTRS:
            header += "%s," % (attr)
        self.csv.write(header.rstrip(",") + "\n")
        self.csv.flush()


    def __write_csv_event(self, e):

        event = ''

        for attr in e.EVENT_ATTRS:
            if e[attr] is not None:
                event += "%s," % (string.replace(e[attr], ',', ' '))

            else:
                event += ","

        self.csv.write(event.rstrip(',') + "\n")
        self.csv.flush()


    def event(self, e, priority=0, policy='all'):

        if self.activated:
            if e["event_type"] == "event":
                self.__write_csv_event(e)


    def shutdown(self):
        logger.info("Closing CSV file..")
        self.csv.flush()
        self.csv.close()
        self.activated = False


class OutputDB(OutputPlugins):

    from Database import DatabaseConn

    def __init__(self, conf):
        logger.info("Added Database output")
        logger.debug("OutputDB options: %s" % (conf.hitems("output-db")))

        self.conf = conf

        type = self.conf.get('output-db', 'type')
        host = self.conf.get('output-db', 'host')
        base = self.conf.get('output-db', 'base')
        user = self.conf.get('output-db', 'user')
        password = self.conf.get('output-db', 'pass')

        self.conn = OutputDB.DatabaseConn()
        self.conn.connect(type, host, base, user, password)
        self.activated = True


    def event(self, e, priority=0, policy='all'):

        if self.conn is not None and e["event_type"] == "event" \
           and self.activated:

            # build query
            query = 'INSERT INTO event ('

            for attr in e.EVENT_ATTRS:
                query += "%s," % (attr)

            query = query.rstrip(',')
            query += ") VALUES ("

            for attr in e.EVENT_ATTRS:
                value = ''

                if e[attr] is not None:
                    value = e[attr]

                query += "'%s'," % (value)

            query = query.rstrip(',')
            query += ");"

            logger.debug(query)

            try:
                self.conn.exec_query(query)

            except Exception, e:
                logger.error(": Error executing query (%s)" % (e))


    def shutdown(self):
        logger.info("Closing database connection..")
        self.conn.close()
        self.activated = False


class OutputIDM(OutputPlugins):

    def __init__(self, conn):
        logger.info("Added IDM output")
        self.conn = conn
        self.activated = True
        self.ip = self.conn.get_idm_ip()

    def event(self, e, priority=0, policy='all'):
        if self.activated:
            try:
                if self.conn.get_is_alive():
                    self.conn.send(str(e))
            except:
                pass

    def shutdown(self):
        logger.info("Closing IDM connection");
        self.conn.close()
        self.activated = False
class Output:
    """Different ways to log ossim events (Event objects)."""

    _outputs = []
    _IDMoutputs = []
    plain_output = server_output = server_output_pro = csv_output = db_output = idm_output = False
    _priority = 0
    _printEvents = True
    _shutdown = False
    def print_ouput_events(value):
        logger.debug("Setting printEvents to %s" % value)
        Output._printEvents = value
    print_ouput_events = staticmethod(print_ouput_events)
    def set_priority(priority):
        Output._priority = priority
    set_priority = staticmethod(set_priority)

    def get_current_priority():
        return Output._priority
    get_current_priority = staticmethod(get_current_priority)


    def add_plain_output(conf):
        if Output.plain_output is False:
            Output._outputs.append(OutputPlain(conf))
            Output.plain_output = True

    add_plain_output = staticmethod(add_plain_output)


    def add_server_output(conn, priority, sendEvents):
        Output._outputs.append(OutputServer(conn, priority, sendEvents))
    add_server_output = staticmethod(add_server_output)



    def add_csv_output(conf):
        if Output.csv_output is False:
            Output._outputs.append(OutputCSV(conf))
            Output.csv_output = True

    add_csv_output = staticmethod(add_csv_output)


    def add_db_output(conf):
        if Output.db_output is False:
            Output._outputs.append(OutputDB(conf))
            Output.db_output = True

    add_db_output = staticmethod(add_db_output)


    def add_idm_output(conn):
        Output._IDMoutputs.append(OutputIDM(conn))
    add_idm_output = staticmethod(add_idm_output)
    
    def event(e, policy='all'):
        if Output._shutdown:
            return
        if Output._printEvents:
            logger.info(str(e).rstrip())
        output_list = Output._outputs 
        if isinstance(e, EventIdm):
            output_list = Output._IDMoutputs
        for output in output_list:
            output.event(e, Output.get_current_priority(),policy)

    event = staticmethod(event)


    def plugin_state(msg):
        logger.info(str(msg).rstrip())

        for output in Output._outputs:
            output.plugin_state(msg)

    plugin_state = staticmethod(plugin_state)


    def shutdown():
        Output._shutdown = True
        for output in Output._outputs:
            output.shutdown()

    shutdown = staticmethod(shutdown)

    def startCacheThread():
        for output in Output._outputs:
            if isinstance(output, OutputServer):
                output.startCacheThread()
    startCacheThread = staticmethod(startCacheThread)
            


if __name__ == "__main__":

    event = Event()
    Output.add_server_output()
    Output.event(event)
    Output.add_csv_output()
    Output.event(event)

# vim:ts=4 sts=4 tw=79 expandtab:
