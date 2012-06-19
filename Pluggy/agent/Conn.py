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
import os, re, socket, string, sys, thread, time
import threading
#
# LOCAL IMPORTS
#
from Config import Conf, Plugin
from Control import ControlManager
from Event import WatchRule
from Logger import *
from MonitorScheduler import MonitorScheduler
from Stats import Stats
import Utils
from Watchdog import Watchdog
from __init__ import __version__

#
# GLOBAL VARIABLES
#
logger = Logger.logger
MAX_TRIES = 3
last_ping  = 0
lock_last_ping = threading.Lock()

class ServerConn:

    __conn = None

    MSG_CONNECT = 'connect id="%s" ' + \
                          'type="sensor" ' + \
                          'version="' + __version__ + '"\n'
    MSG_APPEND_PLUGIN = 'session-append-plugin id="%s" ' + \
                          'plugin_id="%s" enabled="%s" state="%s"\n'
    MSG_GET_FRAMEWORK = 'server-get-framework\n'
    def __init__(self, server_ip, server_port, priority, allow_frmk_data, sendEvents, plugins, stopEvent):
        #self.conf = conf
        self.plugins = plugins
        self.sequence = 0
        self.server_ip = server_ip
        self.server_port = server_port
        self.allow_frmk_data = allow_frmk_data
        self.monitor_scheduler = MonitorScheduler()
        self.monitor_scheduler.start()
        self.__patternFrmkMessageResponse = re.compile("server_ip=\"(?P<srv_ip>(?:[\d]{1,3})\.(?:[\d]{1,3})\.(?:[\d]{1,3})\.(?:[\d]{1,3}))\" server_name=\"(?P<srv_name>([^\"]+))\" server_port=\"(?P<srv_port>\d+)\" framework_ip=\"(?P<frmk_ip>(?:[\d]{1,3})\.(?:[\d]{1,3})\.(?:[\d]{1,3})\.(?:[\d]{1,3}))\" framework_name=\"(?P<frmk_name>([^\"]+))\" framework_port=\"(?P<frmk_port>\d+)\"")
        self.frmk_hostname = ''
        self.frmk_ip = ''
        self.frmk_port = ''
        self.priority = priority
        self.__isAlive = False
        self.__stopped = False
        self.__sendEvents = sendEvents
        self.__keep_working = True
        self.__validFrmkData = False
        self.__threadControlMessages = None
        self.__controlMsgThread_isRunning = False
        self.__stopEvent = stopEvent
        self.__runningConnect = False
        self.__conn = None
        self.__writeLock = threading.RLock()

    def connect(self, attempts=3, waittime=10.0):
        '''
        connect to server:
            - attempts == 0 means that agent try to connect forever
            - waittime = seconds between attempts
        '''
        self.__keep_working = True
        self.sequence = 1
        count = 1

        if self.__conn is None:

            logger.info("Connecting to server (%s, %s).." % (self.server_ip, self.server_port))
            while self.__keep_working and not self.__isAlive:
                if self.__stopEvent.isSet():                    
                    self.close()
                    continue
                self.__connect_to_server()
                if self.__conn is not None:
                    self.__append_plugins()
                    self.control_messages()
                    if self.allow_frmk_data:
                        self.frmk_hostname, self.frmk_ip, self.frmk_port = self.__get_framework_connection_data()
                        logger.debug("Server (%s:%s) Framework Connection Data FRMK_HN:%s, FRMK_IP:%s, FRMK_PORT:%s" % (self.server_ip, self.server_port, self.frmk_hostname, self.frmk_ip, self.frmk_port))
                    elif not self.__validFrmkData :
                        logger.info("This server (%s:%s) doesn't support framework data connection" % (self.server_ip, self.server_port))
                    break

                else:
                    logger.info("Can't connect to server, " + \
                                "retrying in %d seconds" % (waittime))
                    #if self.__keep_working:
                    time.sleep(waittime)

                # check #attempts
                if attempts != 0 and count == attempts:
                    break
                count += 1

        else:
            logger.info("Reusing server connection (%s, %s).." \
                % (self.server_ip, self.server_port))
        return self.__conn


    def close(self):


        self.__keep_working = False
        self.__isAlive = False
        if self.__conn is not None:
            logger.info("Closing server connection..")
            try:
                self.__conn.shutdown(socket.SHUT_RDWR)
                self.__conn.close()
            except Exception,e:
                pass
        self.__conn = None
        self.__controlMsgThread_isRunning = False


    def reconnect(self, attempts=0, waittime=10.0):
        '''
            Reset the current connection by closing and reopening it
        '''
        if self.__runningConnect:
            return
        self.close()
        time.sleep(1)
        Stats.server_reconnect(self.server_ip)
        tmptries = 0
        while tmptries < MAX_TRIES:
            if self.connect(attempts, waittime) is not None:
                break
            tmptries += 1
        if tmptries >= MAX_TRIES:
            self.__stopped = True
            self.__keep_working = False


    def send(self, msg):
        if self.__keep_working:
            self.__writeLock.acquire()
            try:
                if self.__isAlive:
                    self.__conn.send(msg)
            except socket.error, e:
                logger.error(e)
                self.close()
                #self.reconnect()
            except AttributeError,e: # self.__conn == None
                logger.error("Atributte Error, %s" % str(e))
                #self.reconnect()
                self.close()
            else:
                logger.debug(msg.rstrip())
            finally:
                self.__writeLock.release()

    def __connect_to_server(self):
        self.__runningConnect = True
        if not self.__keep_working:
            return
        if self.__isAlive:
            return
        data = ""
        self.__conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.__conn.connect((self.server_ip, int(self.server_port)))
            self.__conn.send(self.MSG_CONNECT % (self.sequence))
            logger.debug("Waiting for server..")
            data = self.__conn.recv(1024)
        except socket.gaierror,e:
            logger.error("Socket error: %s" % str(e))
            self.close()
        except socket.herror,e:
            logger.error("Addressing error : %s " % str(e))
            self.close()
        except socket.timeout,e:
            logger.error("TCP timeout on connection try.. ")
            self.close()
        except socket.error, e:
            logger.error(ERROR_CONNECTING_TO_SERVER      % (self.server_ip, str(self.server_port)) + ": " + str(e))
            self.close()
        except Exception, e:
            logger.error("Error connection. %s" % str(e))
            self.close()
        else:
            
            if data == 'ok id="' + str(self.sequence) + '"\n':
                logger.info("Connected to server %s:%s!" % (self.server_ip, self.server_port))
                self.__stopped = False
                self.__keep_working = True
                self.__isAlive = True
            else:
                logger.error("Bad response from server (seq_exp:%s): %s " % (self.sequence,str(data)))
                self.close()
        self.__runningConnect = False
        return self.__conn
    

    def __append_plugins(self):

        logger.debug("Apending plugins..")
        msg = ''

        for plugin in self.plugins:
            self.sequence += 1
            if plugin.getboolean("config", "enable"):
                msg = self.MSG_APPEND_PLUGIN % \
                        (str(self.sequence),
                        plugin.get("config", "plugin_id"),
                        'true', 'start')
            else:
                msg = self.MSG_APPEND_PLUGIN % \
                        (str(self.sequence),
                        plugin.get("config", "plugin_id"),
                        'false', 'stop')
            self.send(msg)


    def recv_line(self):

        char = data = ''
        keep_reading = True
        while keep_reading and self.__isAlive:
            try:
                char = self.__conn.recv(1)
                data += char
                if char == '\n':
                    keep_reading = False
            except socket.timeout, e:
                pass
            except socket.error, e:
                logger.error('Error receiving data from server: ' + str(e))
                keep_reading = False
                self.close()
#                time.sleep(10)
#                self.reconnect()
            except AttributeError,e:
                logger.error('Error receiving data from server - Attribute Error: %s' % str(e))
                keep_reading = False
                self.close()
                #time.sleep(10)
                #self.reconnect()

        return data


    def __recv_control_messages(self):

        ####### watch-rule test #######
        if (0):
            time.sleep(1)
            data = 'watch-rule plugin_id="2005" ' + \
               'plugin_sid="246" condition="gt" value="1" ' + \
               'from="127.0.0.1" to="127.0.0.1" ' + \
               'port_from="4566" port_to="22"'
            self.__control_monitors(data)
        ###############################

        while self.__keep_working and self.__isAlive:

            try:
                # receive message from server (line by line)
                data = self.recv_line()
                logger.info("Received message from server: " + data.rstrip())

                # 1) type of control messages: plugin management
                #    (start, stop, enable and disable plugins)
                #
                if data.startswith(Watchdog.PLUGIN_START_REQ) or \
                   data.startswith(Watchdog.PLUGIN_STOP_REQ) or \
                   data.startswith(Watchdog.PLUGIN_ENABLE_REQ) or \
                   data.startswith(Watchdog.PLUGIN_DISABLE_REQ):

                    self.__control_plugins(data)

                # 2) type of control messages: watch rules (monitors)
                #
                elif data.startswith('watch-rule'):
                    self.__control_monitors(data)

                # 3) type of control messages: ping
                elif data.startswith('ping'):
                    logger.info("Response: pong")
                    self.send('pong\n')

            except Exception, e:
                logger.error(
                    'Unexpected exception receiving from server: ' + str(e))
            time.sleep(0.01)
        logger.info("Ends control message thread..")
        self.__controlMsgThread_isRunning = False


    def __control_plugins(self, data):

        # get plugin_id of process to start/stop/enable/disable
        pattern = re.compile('(\S+) plugin_id="([^"]*)"')
        result = pattern.search(data)
        if result is not None:
            (command, plugin_id) = result.groups()
        else:
            logger.warning("Bad message from server: %s" % (data))
            return

        # get plugin from plugin list searching by the plugin_id given
        for plugin in self.plugins:
            if int(plugin.get("config", "plugin_id")) == int(plugin_id):

                if command == Watchdog.PLUGIN_START_REQ:
                    Watchdog.start_process(plugin)

                elif command == Watchdog.PLUGIN_STOP_REQ:
                    Watchdog.stop_process(plugin)

                elif command == Watchdog.PLUGIN_ENABLE_REQ:
                    Watchdog.enable_process(plugin)

                elif command == Watchdog.PLUGIN_DISABLE_REQ:
                    Watchdog.disable_process(plugin)

                break


    def __control_monitors(self, data):

        # build a watch rule, the server request.
        watch_rule = WatchRule()
        for attr in watch_rule.EVENT_ATTRS:
            pattern = ' %s="([^"]*)"' % (attr)
            result = re.findall(pattern, data)
            if result != []:
                watch_rule[attr] = result[0]

        for plugin in self.plugins:

            # look for the monitor to be called
            if plugin.get("config", "plugin_id") == watch_rule['plugin_id'] and\
               plugin.get("config", "type").lower() == 'monitor':

                self.monitor_scheduler.\
                    new_monitor(type=plugin.get("config", "source"),
                                plugin=plugin,
                                watch_rule=watch_rule)
                break


    def control_messages(self):
        '''
        Launch new thread to manage control messages
        '''
        #thread.start_new_thread(self.__recv_control_messages, ())
        self.__controlMsgThread_isRunning = True
        self.__threadControlMessages = threading.Thread(target=self.__recv_control_messages, args=())
        self.__threadControlMessages.start()


    def get_allow_frmk_data(self):
        return self.allow_frmk_data


    def get_is_alive(self):
        return self.__isAlive


    def get_server_ip(self):
        return self.server_ip


    def get_server_port(self):
        return self.server_port


    def get_send_events(self):
        return self.__sendEvents


    def get_has_valid_frmkdata(self):
        return self.__validFrmkData


    def __get_framework_connection_data(self):
        frmk_ip = ""
        frmk_port = ""
        frmk_hostname = ""
        data = ""
        
        if self.__validFrmkData:
            return self.frmk_hostname, self.frmk_ip, self.frmk_port
            
        if self.__conn is not None:
            try:
                logger.info("Waiting for framework connection data from %s:%s" % (self.server_ip, self.server_port))
                self.__conn.send(ServerConn.MSG_GET_FRAMEWORK)
                data = self.__conn.recv(1024)
                time.sleep(1)
            except socket.error, e:
                logger.error("Socket (%s:%s) is down. Error_str: %s" % (self.server_ip, self.server_port, str(e)))
                self.__conn = None
            if not data:
                logger.error("No reponse for 'server-get-framework' request")

            else:
                response_data = self.__patternFrmkMessageResponse.match(data)
                if response_data is not None:
                    frmk_ip = response_data.group('frmk_ip')
                    frmk_hostname = response_data.group('frmk_name')
                    frmk_port = response_data.group('frmk_port')
                    self.__validFrmkData = True
                else:
                    logger.error("Bad reponse for 'server-get-framework' request")
        else:
            logger.error("I'm not connected!")
        self.frmk_hostname = frmk_hostname
        self.frmk_ip = frmk_ip
        self.frmk_port = frmk_port
        return frmk_hostname, frmk_ip, frmk_port


    def get_framework_data(self):
        return self.frmk_hostname, self.frmk_ip, self.frmk_port


    def get_priority(self):
        return self.priority


    def get_is_stopped(self):
        return self.__stopped

    def set_framework_data(self, hostname, ip, port):
        self.__validFrmkData = True
        self.frmk_hostname = hostname
        self.frmk_ip = ip
        self.frmk_port = port

class IDMConn:

    MSG_CONNECT = 'connect id="%s" type="sensor" version="' + __version__ + '"\n'

    def __init__(self, idm_ip, idm_port, queue = None):
        self.__idm_ip = idm_ip
        self.__idm_port = idm_port
        self.__conn = None
        self.__isAlive = False
        self.__queue = queue
        self.__controlMsgThread_isRunning = False

    def connect(self):
        self.__conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.__conn.settimeout(10)
        try:
            logger.info("Connecting to IDM server (%s:%s)"  % (self.__idm_ip,self.__idm_port))
            self.__conn.connect((self.__idm_ip, int(self.__idm_port)))            
        except Exception,e:
            logger.error("Can't connect to IDM server..:%s" % str(e))
            self.close()
        else:
            logger.info("Connected to IDM server (%s:%s)" % (self.__idm_ip,self.__idm_port))
            self.__isAlive = True
            self.sendMsgQueue("idmconnected\n")
        return


    def get_idm_ip(self):
        return self.__idm_ip


    def get_idm_port(self):
        return self.__idm_port


    def sendMsgQueue(self,data):
        if self.__queue is not None:
            self.__queue.put_nowait(data)
    def send(self,data):
        global lock_last_ping
        global last_ping
        if not self.__isAlive:
            logger.info("Can't send IDM message, sever is down?")
        else:
            lock_last_ping.acquire()
            last_ping = time.time()
            lock_last_ping.release()
            logger.debug("Sending IDM event:%s" % data)
            try:
                self.__conn.send(data)
            except Exception,e:
                logger.error("IDM down?  %s:" % str(e))
                self.close()
                self.sendMsgQueue("idmdesconnected\n")
    def start_control(self):
        self.__startControlMessages()
    def get_is_alive(self):
        return self.__isAlive

    def __recv_line(self):
        char = data = ''
        keep_reading = True
        while keep_reading:
            try:
                char = self.__conn.recv(1)
                data += char
                if char == '\n':
                    keep_reading = False
            except socket.timeout, e:
                pass
            except socket.error, e:
                logger.error('Error receiving data from IDM server: ' + str(e))
                self.__isAlive = False
                time.sleep(10)
            except AttributeError,e:
                logger.error('Error receiving data from IDM server- Attributte Error: %s' % str(e))
                self.__isAlive = False
                time.sleep(10)


        return data


    def __IDM_controlMessages(self):
        conn_time_out = 30
        self.__lastping = time.time()
        global lock_last_ping
        global last_ping
        while self.__isAlive:
            try:
                # receive message from server (line by line)
                data = self.__recv_line()
                if data.rstrip() != '':
                    logger.info("Received message from IDM server: " + data.rstrip())
                if data.startswith('ping'):
                    lock_last_ping.acquire()
                    last_ping = time.time()
                    lock_last_ping.release()
                    self.send('pong\n')
                elif data.startswith('sendme'):
                    lock_last_ping.acquire()
                    last_ping = time.time()
                    lock_last_ping.release()
                    self.sendMsgQueue('sendme')
            except socket.timeout:
                pass
            except Exception, e:
                logger.error(
                    'Unexpected exception receiving from server: ' + str(e))
            time_without_ping =time.time()-last_ping
            if time_without_ping > conn_time_out:
                logger.error("30s without IDM ping (%s:%s)... closing connection" %(self.__idm_ip,self.__idm_port))
                self.close()
            time.sleep(1)
        logger.info("Ends IDM control message thread..")
        self.__controlMsgThread_isRunning = False


    def __startControlMessages(self):
        self.__controMessageThread_isRunning = True
        self.__threadControlMessages = threading.Thread(target=self.__IDM_controlMessages, args=())
        self.__threadControlMessages.start()

    def controlThreadRunning(self):
        return self.__controlMsgThread_isRunning
    def close(self):
        self.__isAlive = False
        if self.__conn:
            logger.info("Closing IDM connection")
            try:
                self.__conn.shutdown(socket.SHUT_RDWR)
                self.__conn.close()
            except:
                pass
        self.__controlMsgThread_isRunning = False

class FrameworkConn():

    __conn = None
    __controlmanager = None


    MSG_CONNECT = 'control id="%s" action="connect" version="' + __version__ + '"\n'


    def __init__(self, conf, frmk_id, frmk_ip, frmk_port):
        self._framework_id = frmk_id #conf.get("control-framework", "id")
        self._framework_ip = frmk_ip#conf.get("control-framework", "ip")
        self._framework_port = frmk_port #conf.get("control-framework", "port")
        self._framework_ping = True
        self.__keep_processing = True
        # instatiate the control manager
        self.__controlmanager = ControlManager(conf)
        self.__alive = False
        self.__pingThread = None
        self.__reciverControlMsgThread = None
        #self.__event = threading.Event()
        self.__tryReconect = False

    # connect to framework daemon
    #  attempts == 0 means that agent try to connect forever
    #  waittime = seconds between attempts
    def connect(self, attempts=0, waittime=10.0):

        # connection attempt counter
        count = 0

        if self.__conn is None:

            logger.info("Connecting to control framework (%s:%s) ..." \
                % (self._framework_ip, self._framework_port))

            while attempts == 0 or count < attempts:
                self.__connect_to_framework()

                if self.__conn is not None:
                    break

                else:
                    logger.info("Can't connect to control framework, " + \
                                "retrying in %d seconds" % (waittime))
                    if self.__keep_processing:
                        time.sleep(waittime)
                    else:
                        count = attempts

                count += 1

        else:
            logger.info("Reusing control framework connection (%s:%s) ..." \
                % (self._framework_ip, self._framework_port))

        return self.__conn


    def close(self):
        logger.info("Closing control framework connection ...")
        if self.__reciverControlMsgThread is not None:
            self.__reciverControlMsgThread.join(1)
        if self.__pingThread is not None:
            self.__pingThread.join(1)
        if self.__conn is not None:
            try:
                self.__conn.shutdown(socket.SHUT_RDWR)
                self.__conn.close()
            except:
                pass
        self.__conn = None
        self.__keep_processing = False
        self.__alive = False
        self.__controlmanager.stopProcess()



    # Reset the current connection by closing and reopening it
    def reconnect(self, attempts=0, waittime=10.0):
        #self.__event.set()
        if self.__conn is not None:
            self.__conn.close()
            self.__conn = None        
        time.sleep(2)
        while self.__keep_processing:
            if self.connect(attempts, waittime) is not None:
                #self.__event.clear()
                break        


    def send(self, msg):
        if self.__keep_processing:
            try:
                self.__conn.sendall(msg)

            except socket.error, e:
                logger.error(e)                
                if not self.__tryReconect:
                    self.__alive = False
                    #self.reconnect()                    

            except AttributeError, e: # self.__conn == None
                if not self.__tryReconect:
                    self.__alive = False                    
                    #self.reconnect()                    

            else:
                logger.debug(msg.rstrip())
                return


    def __connect_to_framework(self):
        self.__conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # establish a 15 seconds timeout on the socket
        self.__conn.settimeout(15)

        data = ""

        try:
            self.__conn.connect((self._framework_ip, int(self._framework_port)))
            self.__conn.send(self.MSG_CONNECT % self._framework_id)

            logger.debug("Waiting for control framework ...")

            data = self.__conn.recv(1024)

        except socket.timeout, e:
            logger.error("Timed out (%us) waiting for the control framework!" % self.__conn.gettimeout())
            self.__conn = None
            self.__alive = False
        except socket.error, e:
            logger.error("Unable to connect to the control framework!")
            self.__conn = None
            self.__alive = False
        else:
            if data == 'ok id="' + str(self._framework_id) + '"\n':
                logger.info("Connected to the control framework (%s:%s) !" % (self._framework_ip, self._framework_port))
                self.__alive = True
            else:
                logger.error("Bad response from the control framework: %s" % (str(data)))
                self.__conn = None
                self.__alive = False


    def __recv_line(self):

        char = data = ''
        keep_reading = True
        while keep_reading and self.__alive:
            try:
                char = self.__conn.recv(1)
            except socket.timeout, e:
                pass
            except socket.error, e:
                logger.error('Error receiving data from the control framework: %s' % str(e))
                self.__alive = False
                self.__conn = None
            except AttributeError:
                logger.error('Atterror; Error receiving data from the control framework!')
                self.__alive = False
                self.__conn = None
            else:
                data += char
                if char == '\n':
                    keep_reading = False
                elif char == '':
                    logger.warning('Connection to the control framework appears to be down.')
                    keep_reading = False
                    char = data = ''
                    self.__alive = False
                    self.__conn = None
                                  
        return data


    # receive control messages from the framework daemon
    def __recv_frmk_control_messages(self):
        
        while self.__keep_processing and self.__alive:
            # receive message from server (line by line)
            data = self.__recv_line().rstrip('\n')
            if data == '':
                continue
            response = self.__controlmanager.process(self.__conn, data)
            # send out all items in the response queue
            while len(response) > 0:
                self.send(response.pop(0))
            time.sleep(1)
        logger.info("Closing thread - receive control framework control messages...!")

    def __ping(self):
        while self.__keep_processing:
            self.send("ping\n")
            time.sleep(60)
    def frmk_alive(self):
        return self.__alive
    def get_frmkip(self):
        return self._framework_ip
    # launch new thread to manage control messages
    def frmk_control_messages(self):
        #thread.start_new_thread(self.__recv_frmk_control_messages, ())
        self.__reciverControlMsgThread = threading.Thread(target=self.__recv_frmk_control_messages, args=())
        self.__reciverControlMsgThread.start()
        # enable keep-alive pinging if appropriate
        if self._framework_ping:
            self.__pingThread = threading.Thread(target=self.__ping, args=())
            self.__pingThread.start()
            


# vim:ts=4 sts=4 tw=79 expandtab:

