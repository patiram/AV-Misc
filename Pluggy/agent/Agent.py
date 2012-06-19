#!/usr/bin/env python
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
import sys
import time
import signal
#import string
import thread
import re
import threading
import socket
import codecs
import Queue
#
# LOCAL IMPORTS
#
from Config import Conf, Plugin, Aliases, CommandLineOptions
from ParserLog import ParserLog
from Watchdog import Watchdog
from Logger import Logger
from Output import Output
from Stats import Stats
from Conn import ServerConn, IDMConn, FrameworkConn
from Exceptions import AgentCritical
from ParserUnifiedSnort import ParserUnifiedSnort
from ParserDatabase import ParserDatabase
from ParserWMI import ParserWMI
from ParserSDEE import ParserSDEE
from ParserRemote import ParserRemote
from ParserUtil import HostResolv
from ParserFtp import ParserFTP
from ParserSnortSyslogBin import ParserSnortSyslogBin

#import pdb
#
# GLOBAL VARIABLES
#
logger = Logger.logger

#Less priority
MAX_PRIORITY_VALUE = 5
# More priroriy
MIN_PRIORITY_VALUE = 0
class Agent:

    def __init__(self):
        '''
        Constructor
        '''
        # parse command line options
        self.options = CommandLineOptions().get_options()
        # read configuration
        self.conf = Conf()
        if self.options.config_file:
            self.__conffile = self.options.config_file
        else:
            self.__conffile = self.conf.DEFAULT_CONFIG_FILE
        self.conf.read([self.__conffile], 'latin1')
        # aliases
        self.__aliases = []
        # list of plugins and total number of rules within them
        self.__plugins = []
        self.__nrules = 0
        self.__plugins_threads = []
        self.__watchdog = None
        self.__shutdown_running = False
        self.__outputIDMConnections = []
        self.__outputServerConnecitonList = []
        self.__frameworkConnecitonList = []
        self.__continue_loading = False
        self.__keep_working = True
        self.__currentPriority = 0
        self.__checkThread = None
        self.__stop_server_counter_array = {}
        self.__output_dic = {}
        self.__output_idm_dic = {}
        self.__pluginStopEvent = threading.Event()
        self.__idm_queue = Queue.Queue()
        self.__timeBeetweenChecks = 2.0
        self.__maxStopCounter = 2
        self.__failovertime = 30.0
        self.__SendPolicyHash = {}

    def __readCustomPluginFunctions(self, plugin, custom_plugin_functions_file):
        pid = plugin.get("DEFAULT", "plugin_id")
        logger.info("Loading custom plugin functions for pid: %s" % pid)
        if os.path.isfile(custom_plugin_functions_file):
            f = open(custom_plugin_functions_file, 'rb')
            lines = f.read()
            result = re.findall("Start Function\s+(\w+)\n(.*?)End Function", lines, re.M | re.S)
            function_list = {}
            for name, function in result:
                logger.info("Loading function: %s" % name)
                try:
                    exec function.strip() in function_list
                    function_name = "%s_%s" % (name, pid)
                    logger.info("Adding function :%s" % function_name)
                    setattr(Plugin, function_name, function_list[name])
                except Exception, e:
                    logger.error("Custom function error: %s" % str(e))
        else:
            logger.warning("Custom plugin functions file does not exist!")

    def __loadPluginPolicies(self):
        if self.conf.has_section('send-policy'):
            logger.info("Loading plugins policies...")
            for plugin_name, policy in self.conf.hitems("send-policy").iteritems():
                logger.info("Plugin policy: %s - %s" % (plugin_name, policy))
                self.__SendPolicyHash[plugin_name] = policy
    def __loadPluginConfigurations(self):
        '''
        Loads plugins's configurations.
        '''
        for name, path in self.conf.hitems("plugins").iteritems():
            #check if there's encondign info.
            self.__SendPolicyHash[name] = "all"
            data = path.split('|')
            path = data[0]
            encoding = 'latin1'
            if len(data) > 1:
                path = data[0]
                encoding = data[1]
                if data[1] != '':
                    encoding = data[1]
                try:
                    encoder = codecs.lookup(encoding)
                    logger.info("Using encoding: %s for plugin: %s" % (encoding, path))
                except LookupError, e:
                    logger.warning("Invalid encoding:%s, using defualt encoding ...latin1")
                    encoding = 'latin1'

            if os.path.exists(path):
                plugin = Plugin()
                # Now read the config file
                plugin.read([path], encoding)
                plugin.set('config', 'encoding', encoding)
                if not plugin.get_validConfig():
                    logger.error("Invalid plugin. Please check it :%s" % path)
                    continue

                #check if custom plugin configuration exist
                custompath = "%s.local" % path
                if os.path.exists(custompath):
                    logger.warning("Loading custom configuration for plugin: %s" % custompath)
                    custom_plug = Plugin()
                    custom_plug.read([custompath], encoding, False)
                    for item in custom_plug.hitems("DEFAULT"):
                        new_value = custom_plug.get("DEFAULT", item)
                        old_value = plugin.get("DEFAULT", item)
                        if new_value != old_value:
                            plugin.set("DEFAULT", item, new_value)
                            logger.warning("Loading custon value for %s--->%s. New value: %s - Old value: %s" % ("DEFAULT", item, new_value, old_value))

                    for section in custom_plug.sections():
                        for item in custom_plug.hitems(section):
                            new_value = custom_plug.get(section, item)
                            if plugin.has_section(section):
                                old_value = plugin.get(section, item)
                            else:
                                old_value = ""

                            if new_value != old_value:
                                if not plugin.has_section(section):
                                    plugin.add_section(section)
                                plugin.set(section, item, new_value)
                                logger.warning("Loading custon value for %s--->%s. New value: %s - Old value: %s" % (section, item, new_value, old_value))
                self.__nrules += len(plugin.sections()) \
                               - plugin.sections().count('translation') \
                               - 1 # [config]

                plugin.set("config", "name", name)
                plugin.replace_aliases(self.__aliases)
                plugin.replace_config(self.conf)
                self.__plugins.append(plugin)
                self.__nrules += len(plugin.sections()) \
                               - plugin.sections().count('translation') \
                               - 1 # [config]
                if plugin.has_option('config', 'custom_functions_file'):
                    self.__readCustomPluginFunctions(plugin, plugin.get('config', 'custom_functions_file'))
            else:
                logger.error("Unable to read plugin configuration (%s) at (%s)" % (name, path))


    def __loadAliases(self, configuration_file):
        '''
        Loads aliases configuration file.
        '''
        self.__aliases = Aliases()
        self.__aliases.read([os.path.join(os.path.dirname(configuration_file), "aliases.cfg")], 'latin1')
        local_aliases_fn = os.path.join(os.path.dirname(configuration_file), "aliases.local")
        #if aliases.local exists, after we've loaded aliases default file, 
        #we load aliases.local
        if os.path.isfile(local_aliases_fn):
            logger.info("Reading local aliases file: %s" % local_aliases_fn)
            self.__aliases.read(local_aliases_fn, 'latin1')


    def __loadOutputProperties(self):
        '''
        Loads output properties values.
        '''
        if self.conf.has_section("output-properties"):
            if self.conf.get("output-properties", "timeBeetweenChecks") != "":
                try:
                    self.__timeBeetweenChecks = float(self.conf.get("output-properties", "timeBeetweenChecks"))
                except ValueError:
                    self.__timeBeetweenChecks = 2.0
            if self.conf.get("output-properties", "maxStopCounter") != "":
                try:
                    self.__maxStopCounter = float(self.conf.get("output-properties", "maxStopCounter"))
                except ValueError:
                    self.__maxStopCounter = 2.0
            if self.conf.get("output-properties", "failovertime") != "":
                try:
                    self.__failovertime = float(self.conf.get("output-properties", "failovertime"))
                except ValueError:
                    self.__failovertime = 30.0


    def __setShutDownRunning(self, value):
        self.__shutdown_running = value


    def __getShutDownRunning(self):
        return self.__shutdown_running


    def __init_logger(self):
        """Initiate the logger. """

        # open file handlers (main and error logs)
        if self.conf.has_option("log", "file"):
            Logger.add_file_handler(self.conf.get("log", "file"))

        if self.conf.has_option("log", "error"):
            Logger.add_error_file_handler(self.conf.get("log", "error"))

        if self.conf.has_option("log", "syslog"):
            if (self.conf.get("log", "syslog")):
                Logger.add_syslog_handler((self.conf.get("log", "syslog"), 514))

        # adjust verbose level
        verbose = self.conf.get("log", "verbose")
        if self.options.verbose is not None:
            # -v or -vv command line argument
            #  -v -> self.options.verbose = 1
            # -vv -> self.options.verbose = 2
            for i in range(self.options.verbose):
                verbose = Logger.next_verbose_level(verbose)

        Logger.set_verbose(verbose)


    def __init_stats(self):
        '''
            Initialize Stats 
        '''
        Stats.startup()

        if self.conf.has_section("log"):
            if self.conf.has_option("log", "stats"):
                Stats.set_file(self.conf.get("log", "stats"))


    def __init_output(self):
        '''
            Initialize Outputs
        '''

        printEvents = True

        if self.conf.has_section("output-properties"):
            if self.conf.has_option("output-properties", "printEvents"):
                printEvents = self.conf.getboolean("output-properties", "printEvents")
        Output.print_ouput_events(printEvents)


        if self.conf.has_section("output-plain"):
            if self.conf.getboolean("output-plain", "enable"):
                Output.add_plain_output(self.conf)

        # output-server is enabled in connect_server()
        # if the connection becomes availble

        if self.conf.has_section("output-csv"):
            if self.conf.getboolean("output-csv", "enable"):
                Output.add_csv_output(self.conf)

        if self.conf.has_section("output-db"):
            if self.conf.getboolean("output-db", "enable"):
                Output.add_db_output(self.conf)
        Output.set_priority(0)
        self.__currentPriority = 0


    def __is_frmk_in_list(self, frmk_ip):
        for fmk in self.__frameworkConnecitonList:
            if fmk.get_frmkip() == frmk_ip:
                return True
        return False


    def __get_frmk(self, frmk_ip):
        for fmk in self.__frameworkConnecitonList:
            if fmk.get_frmkip() == frmk_ip:
                return fmk
        return None


    def __get_is_srv_in_list(self, srv_ip):
        for srv in self.__outputServerConnecitonList:
            if srv.get_server_ip() == srv_ip:
                return True
        return False


    def __connect_framework(self):
        '''
            Request each server connection for its framework data and try to connect it.
        '''
        conn_counter = 0

        for server_conn in self.__outputServerConnecitonList:
            if server_conn.get_priority() <= self.__currentPriority \
            and server_conn.get_is_alive() and server_conn.get_has_valid_frmkdata():

                frmk_tmp_id, frmk_tmp_ip, frmk_tmp_port = server_conn.get_framework_data()
                tmpFrameworkConn = None
                tryConnect = False
                inlist = self.__is_frmk_in_list(frmk_tmp_ip)
                if not inlist:
                    tmpFrameworkConn = FrameworkConn(self.conf, frmk_tmp_id, frmk_tmp_ip, frmk_tmp_port)
                    tryConnect = True
                else:
                    tmpFrameworkConn = self.__get_frmk(frmk_tmp_ip)
                    if tmpFrameworkConn is not None and not tmpFrameworkConn.frmk_alive():
                        tryConnect = True
                    else:
                        conn_counter = 1

                if tryConnect:
                    if tmpFrameworkConn.connect(attempts=3, waittime=30):
                        logger.debug("Control Framework (%s:%s) is now enabled!" % (frmk_tmp_ip, frmk_tmp_port))
                        conn_counter += 1
                        tmpFrameworkConn.frmk_control_messages()
                        if not inlist:
                            self.__frameworkConnecitonList.append(tmpFrameworkConn)


    def __check_pid(self):
        """Check if a running instance of the agent already exists. """

        pidfile = self.conf.get("daemon", "pid")

        # check for other ossim-agent instances when not using --force argument
        if self.options.force is None and os.path.isfile(pidfile):
            raise AgentCritical("There is already a running instance")

        # remove ossim-agent.pid file when using --force argument
        elif os.path.isfile(pidfile):
            try:
                os.remove(pidfile)

            except OSError, e:
                logger.warning(e)


    def __createDaemon(self):
        """Detach a process from the controlling terminal and run it in the
        background as a daemon.

        Note (DK): Full credit for this daemonize function goes to Chad J. Schroeder.
        Found it at ASPN http://aspn.activestate.com/ASPN/Cookbook/Python/Recipe/278731
        Please check that url for useful comments on the function.
        """
        # Install a handler for the terminate signals
        signal.signal(signal.SIGTERM, self.__terminate)
        # -d command-line argument
        if self.options.daemon:
            self.conf.set("daemon", "daemon", "True")
        if self.conf.getboolean("daemon", "daemon") and \
            self.options.verbose is None:
            logger.info("Forking into background..")
            UMASK = 0
            WORKDIR = "/"
            MAXFD = 1024
            REDIRECT_TO = "/dev/null"
            if (hasattr(os, "devnull")):
                REDIRECT_TO = os.devnull
            try:
                pid = os.fork()
            except OSError, e:
                raise Exception, "%s [%d]" % (e.strerror, e.errno)
                sys.exit(1)
            # check if we are the first child
            if (pid == 0):
                os.setsid()
                # attempt to fork a second child
                try:
                    pid = os.fork()   # Fork a second child.
                except OSError, e:
                    raise Exception, "%s [%d]" % (e.strerror, e.errno)
                    sys.exit(1)
                # check if we are the second child
                if (pid == 0):
                    os.chdir(WORKDIR)
                    os.umask(UMASK)
                # otherwise exit the parent (the first child of the second child)
                else:
                    open(self.conf.get("daemon", "pid"), 'w').write("%d" % pid)
                    os._exit(0)
            # otherwise exit the parent of the first child
            else:
                os._exit(0)
            import resource         # Resource usage information.
            maxfd = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
            if (maxfd == resource.RLIM_INFINITY):
                maxfd = MAXFD
            for fd in range(0, maxfd):
                try:
                    os.close(fd)
                except OSError:      # ERROR, fd wasn't open to begin with (ignored)
                    pass
            os.open(REDIRECT_TO, os.O_RDWR) # standard input (0)
            os.dup2(0, 1)                   # standard output (1)
            os.dup2(0, 2)                   # standard error (2)
            return(0)


    def __init_plugins(self):
        for plugin in self.__plugins:
            if plugin.get("config", "type") == "detector":
                plugin_id = plugin.get("DEFAULT", "plugin_id")
                plugin_name = plugin.get("config", "name")
                plugin.set('config', 'send_policy', 'all')
                if self.__SendPolicyHash.has_key(plugin_name):
                    policy = self.__SendPolicyHash[plugin_name]
                    plugin.set('config', 'send_policy', policy)
                
                if plugin.get("config", "source") == "log":
                    parser = ParserLog(self.conf, plugin)
                    parser.start()
                    self.__plugins_threads.append(parser)
                elif plugin.get("config", "source") == "snortsyslogbin":
                    parser = ParserSnortSyslogBin(self.conf, plugin)
                    parser.start()
                    self.__plugins_threads.append(parser)
                elif plugin.get("config", "source") == "snortlog":
                    parser = ParserUnifiedSnort(self.conf, plugin)
                    parser.start()
                    self.__plugins_threads.append(parser)
                elif plugin.get("config", "source") == "database":
                    parser = ParserDatabase(self.conf, plugin, None)
                    parser.start()
                    self.__plugins_threads.append(parser)
                elif plugin.get("config", "source") == "wmi":
                    #line_cnt = 0
                    try:
                        credentials = open(plugin.get("config", "credentials_file"), "rb")
                    except:
                        logger.warning("Unable to load wmi credentials file %s, disabling wmi collection." % (plugin.get("config", "credentials_file")))
                        plugin.set("config", "enable", "no")
                        continue
                    for row in credentials:
                        creds = row.split(",")
                        parser = ParserWMI(self.conf, plugin, creds[0], creds[1], creds[2])
                        parser.start()
                        self.__plugins_threads.append(parser)
                elif plugin.get("config", "source") == "sdee":
                    try:
                        credentials = open(plugin.get("config", "credentials_file"), "rb")
                    except:
                        logger.warning("Unable to load sdee credentials file, falling back to old behaviour")
                        parser = ParserSDEE(self.conf, plugin)
                        parser.start()
                        self.__plugins_threads.append(parser)
                    else:
                        for row in credentials:
                            creds = row.split(",")
                            parser = ParserSDEE(self.conf, plugin, creds[0], creds[1], creds[2].strip())
                            parser.start()
                            self.__plugins_threads.append(parser)
                elif plugin.get("config", "source") == "remote-log":
                    parser = ParserRemote(self.conf, plugin)
                    logger.info("Starting remote ssh parser")
                    parser.start()
                    self.__plugins_threads.append(parser)
                elif plugin.get("config", "source") == "ftp":
                    parser = ParserFTP(self.conf, plugin, self.__idm_queue)
                    parser.start()
                    self.__plugins_threads.append(parser)
            
        logger.info("%d detector rules loaded" % (self.__nrules))


    def __init_watchdog(self):
        '''
            Starts Watchdog thread
        '''
        if self.conf.getboolean("watchdog", "enable"):
            self.__watchdog = Watchdog(self.conf, self.__plugins)
            self.__watchdog.start()


    def __terminate(self, sig, params):
        '''
            Handle terminate signal
        '''
        if not self.__getShutDownRunning():
            logger.info("WARNING: Shutdown received! - Processing it ...!")
            self.__shutdown()
        else:
            logger.info("WARNING: Shutdown received! - We can't process it because another shutdonw process is running!")


    def __shutdown(self):
        '''
            Handles shutdown signal. Stop all threads, plugist, closes connections...
        '''
        #Disable Ctrl+C signal.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        logger.info("Shutdown in process...")
        self.__setShutDownRunning(True)
        Watchdog.setShutdownRunning(True)
        self.__keep_working = False
        self.__pluginStopEvent.set()
        logger.info("Waiting for check thread..")
        if self.__checkThread is not None:
            self.__checkThread.join(1)

        # Remove the pid file
        pidfile = self.conf.get("daemon", "pid")
        if os.path.exists(pidfile):
            f = open(pidfile)
            pid_from_file = f.readline()
            f.close()

            try:
                # don't remove the ossim-agent.pid file if it 
                # belongs to other ossim-agent process
                if pid_from_file == str(os.getpid()):
                    os.remove(pidfile)

            except OSError, e:
                logger.warning(e)


        # output plugins
        Output.shutdown()

        # parsers
        for parser in self.__plugins_threads:
            if hasattr(parser, 'stop'):
                parser.stop()
        #Stop server connections.
        for srv in self.__outputServerConnecitonList:
            srv.close()
        #Stop IDM connection
        for idm in self.__outputIDMConnections:
            idm.close()
        # close framework connections
        for frmk_conn in self.__frameworkConnecitonList:
            frmk_conn.close()
        # execution statistics        
        Stats.shutdown()
        if Stats.dates['startup']:
            Stats.stats()
        # Watchdog
        if self.__watchdog:
            self.__watchdog.shutdown()
        self.__setShutDownRunning(False)


    def __waitforever(self):
        '''
            Wait forever agent loop
        '''
        timer = 0

        while self.__keep_working:
            time.sleep(1)
            timer += 1

            if timer >= 30:
                Stats.log_stats()
                timer = 0


    def __getServerConn_byIP(self, ip):
        '''
            Read the interal serverconnection list and returns the server with the ip,
            passed as an argument
        '''
        for server in self.__outputServerConnecitonList:
            if server.get_server_ip() == ip:
                return server


    def __addNewOutputConnection(self, server_conn):
        '''
            Adds new ouput server connection
        '''
        if not self.__output_dic.has_key(server_conn.get_server_ip()):
            logger.info("Adding output ... %s:%s" % (server_conn.get_server_ip(), server_conn.get_server_port()))
            self.__outputServerConnecitonList.append(server_conn)
            self.__output_dic[server_conn.get_server_ip()] = 1
            Stats.add_server(server_conn.get_server_ip())
            #First time we puts this counter to max value,
            self.__stop_server_counter_array[server_conn.get_server_ip()] = 9999
            Output.add_server_output(server_conn, server_conn.get_priority(), server_conn.get_send_events())


    def __addNewOutputIDMConnection(self, idm_conn):
        if not self.__output_idm_dic.has_key(idm_conn.get_idm_ip()):
            logger.info("Adding idm output ... %s:%s" % (idm_conn.get_idm_ip(), idm_conn.get_idm_port()))
            self.__outputIDMConnections.append(idm_conn)
            self.__output_idm_dic[idm_conn.get_idm_ip()] = 1
            Output.add_idm_output(idm_conn)


    def __readOuptutServers(self):
        ''' Read the ouptput server list, if exists'''
        if self.conf.has_section("output-server"):
            if self.conf.getboolean("output-server", "enable"):
                server_ip = self.conf.get("output-server", "ip")
                server_port = self.conf.get("output-server", "port")
                server_priority = 0
                allow_frmk_data = True
                sendEvents = True
                hostname = "primary"
                framework_data = False
                framework_ip = ""
                framework_port = 0
                framework_hostname = ""
                if self.conf.has_section("control-framework"):
                    framework_data = True
                    framework_ip = self.conf.get("control-framework", "ip")
                    framework_port = self.conf.get("control-framework", "port")
                    framework_hostname = socket.gethostname()
                tmpServerConnection = ServerConn(server_ip, server_port, server_priority, allow_frmk_data, sendEvents, self.__plugins, self.__pluginStopEvent)
                if framework_data:
                    tmpServerConnection.set_framework_data(framework_hostname, \
                                                           framework_ip, \
                                                           framework_port)

                self.__addNewOutputConnection(tmpServerConnection)
        #Regular expression to parse the readed line
        #data_reg_expr = "(?P<server_ip>(?:[\d]{1,3})\.(?:[\d]{1,3})\.(?:[\d]{1,3})\.(?:[\d]{1,3}));(?P<server_port>[0-9]{1,5});(?P<send_events>True|False|Yes|No);(?P<allow_frmk_data>True|False|Yes|No);(?P<server_priority>[0-5]);(?P<frmk_ip>(?:[\d]{1,3})\.(?:[\d]{1,3})\.(?:[\d]{1,3})\.(?:[\d]{1,3}));(?P<fmrk_port>[0-9]{1,5});(?P<frmk_id>((([a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9\-]*[a-zA-Z0-9])\.)([a-zA-Z])+)))"
        #data_reg_expr ="(?P<server_ip>(?:[\d]{1,3})\.(?:[\d]{1,3})\.(?:[\d]{1,3})\.(?:[\d]{1,3}));(?P<server_port>[0-9]{1,5});(?P<send_events>True|False|Yes|No);(?P<allow_frmk_data>True|False|Yes|No);(?P<server_priority>[0-5]);(?P<frmk_ip>(?:[\d]{1,3})\.(?:[\d]{1,3})\.(?:[\d]{1,3})\.(?:[\d]{1,3}));(?P<fmrk_port>[0-9]{1,5});(?P<frmk_id>(?=.{1,255}$)[0-9A-Za-z](?:(?:[0-9A-Za-z]|\b-){0,61}[0-9A-Za-z])?(?:\.[0-9A-Za-z](?:(?:[0-9A-Za-z]|\b-){0,61}[0-9A-Za-z])?)*\.?)"
        data_reg_expr = "(?P<server_ip>(?:[\d]{1,3})\.(?:[\d]{1,3})\.(?:[\d]{1,3})\.(?:[\d]{1,3}));(?P<server_port>[0-9]{1,5});(?P<send_events>True|False|Yes|No);(?P<allow_frmk_data>True|False|Yes|No);(?P<server_priority>[0-5]);(?P<frmk_ip>(?:[\d]{1,3})\.(?:[\d]{1,3})\.(?:[\d]{1,3})\.(?:[\d]{1,3}));(?P<frmk_port>[0-9]{1,5})"
        pattern = re.compile(data_reg_expr)
        if self.conf.has_section("output-server-list"):
            logger.debug("ouptut-server-list section founded! Reading it!")
            for hostname, data in self.conf.hitems("output-server-list").iteritems():
                value_groups = pattern.match(data)
                if value_groups is not None:
                    server_ip = value_groups.group('server_ip')
                    server_port = int(value_groups.group('server_port'))
                    server_send_events = value_groups.group('send_events')
                    allow_frmk_data = value_groups.group('allow_frmk_data')
                    server_priority = int(value_groups.group('server_priority'))
                    fmk_ip = value_groups.group('frmk_ip')
                    fmk_port = value_groups.group('frmk_port')
                    fmk_id = socket.gethostname() #value_groups.group('frmk_id')
                    logger.debug("Server -> IP: %s , PORT: %s , SEND_EVENTS: %s , ALLOW_FRMK_DATA: %s, PRIORITY:%s" % (server_ip, server_port, server_send_events, allow_frmk_data, server_priority))
                    tmpServerConnection = ServerConn(server_ip, server_port, server_priority, allow_frmk_data, server_send_events, self.__plugins, self.__pluginStopEvent)
                    tmpServerConnection.set_framework_data(fmk_id, fmk_ip, fmk_port)
                    self.__addNewOutputConnection(tmpServerConnection)

                else:
                    logger.warning("Invalid server output (%s = %s),please check your configuration file" % (hostname, data))
        self.__outputServerConnecitonList.sort(cmp=lambda x, y: cmp(x.get_priority(), y.get_priority()))


    def __changePriority(self):
        '''
            Change current server output priority
        '''
        Output.set_priority(self.__currentPriority)
        for frmk_conn in self.__frameworkConnecitonList:
            frmk_conn.close()


    def __check_servers_by_priority(self, prio):
        '''
            Check servers by priority
        '''

        aliveServers = 0
        for server_conn in self.__outputServerConnecitonList:
            if server_conn.get_priority () == prio:
                if server_conn.get_is_alive():
                    aliveServers = aliveServers + 1
                    self.__stop_server_counter_array[ server_conn.get_server_ip()] = 0
                else:
                    #increases stop counter
                    self.__stop_server_counter_array[ server_conn.get_server_ip()] += 1
        return aliveServers


    def __readOutputIDM(self):
        if self.conf.has_section("output-idm"):
            if self.conf.getboolean("output-idm", "enable"):
                idm_ip = self.conf.get("output-idm", "ip")
                idm_port = self.conf.get("output-idm", "port")
                idmconn = IDMConn(idm_ip, idm_port, self.__idm_queue)
                self.__addNewOutputIDMConnection(idmconn)
                #self.__outputIDMConnections.append(idmconn)
                #Output.add_idm_output(idmconn)
        data_reg_expr = "(?P<idm_server_ip>(?:[\d]{1,3})\.(?:[\d]{1,3})\.(?:[\d]{1,3})\.(?:[\d]{1,3}));(?P<idm_server_port>[0-9]{1,5})"
        pattern = re.compile(data_reg_expr)
        if self.conf.has_section("idm-server-list"):
            logger.debug("idm-server-list section founded! Reading it!")
            for hostname, data in self.conf.hitems("idm-server-list").iteritems():
                value_groups = pattern.match(data)
                if value_groups is not None:
                    idm_server_ip = value_groups.group('idm_server_ip')
                    idm_server_port = int(value_groups.group('idm_server_port'))
                    #logger.info("Append IDM Server -> IP: %s , PORT: %s" % (idm_server_ip, idm_server_port))
                    idmConnection = IDMConn(idm_server_ip, idm_server_port, self.__idm_queue)
                    self.__addNewOutputIDMConnection(idmConnection)
                    #self.__outputIDMConnections.append(idmConnection)
                    #Output.add_idm_output(idmConnection)
                else:
                    logger.warning("Invalid server output (%s = %s),please check your configuration file" % (hostname, data))


    def __check_server_status(self):
        '''
            Check if there is any server, with the max priority (temporal priority),  alive.
            If yes and the temporal priority  is greater than current priority, we've to change the priority, if no, we do nothing
        '''
        priority_changed = False
        logger.debug("Check status configuration: Time between checks: %s - max stop counter: %s" % (self.__timeBeetweenChecks, self.__maxStopCounter))
        #priority 0,1,2,3,4,5
        servers_alive_by_priority = [0, 0, 0, 0, 0, 0]
        tmpPriority = 0
        while self.__keep_working:
            if tmpPriority > 5:
                tmpPriority = 0
            for idm in  self.__outputIDMConnections:
                if idm is not None and not idm.get_is_alive():
                    idm.connect()
                    if idm.get_is_alive():
                        idm.start_control()
            priority_changed = False
            aliveServers = 0
            aliveServers = self.__check_servers_by_priority(tmpPriority)
            logger.debug("Priority: :%s Alive Servers:%s" % (tmpPriority, aliveServers))
            servers_alive_by_priority[tmpPriority] = aliveServers
            if aliveServers > 0:
                self.__connect_framework()
            for server_ip, stop_counter in self.__stop_server_counter_array.items():
                logger.debug("Server:%s - stopCounter:%s" % (server_ip, stop_counter))
                if stop_counter >= self.__maxStopCounter:
                    serverconn = self.__getServerConn_byIP(server_ip)
                    logger.info("Server %s:%s has reached %s stops, trying to reconnect!" % (serverconn.get_server_ip(), serverconn.get_server_port(), self.__maxStopCounter))
                    serverconn.connect(attempts=3, waittime=10)
                    Stats.server_reconnect(serverconn.get_server_ip())
                    self.__stop_server_counter_array[server_ip] = 0
                    self.__connect_framework()
                    time.sleep(1)
            #Some server is alive...
            if (tmpPriority < self.__currentPriority) and aliveServers > 0:
                logger.warning("Current priority server has changed, old_priority:%d current priority = %d" % (self.__currentPriority, tmpPriority))
                self.__currentPriority = tmpPriority
                self.__changePriority()
                priority_changed = True
            #check stop counter, if a server reaches five stops, we retry to connect
            if self.__keep_working and not priority_changed:
                time.sleep(self.__timeBeetweenChecks)
            tmpPriority = tmpPriority + 1
        logger.info("Check status server thread finish!")


    def main(self):
        try:
            self.__check_pid()
            self.__createDaemon()
            self.conf.read([self.__conffile], 'latin1')
            self.__loadAliases(configuration_file=self.__conffile)
            self.__loadPluginConfigurations()
            self.__loadPluginPolicies()
            self.__loadOutputProperties()
            self.__readOuptutServers()
            self.__readOutputIDM()
            self.__init_logger()
            self.__init_output()
            self.__init_stats()
            self.__checkThread = threading.Thread(target=self.__check_server_status, args=())
            self.__checkThread.start()
            self.__init_plugins()
            self.__init_watchdog()
            HostResolv.loadHostCache()
            Output.startCacheThread()
            self.__waitforever()

        except KeyboardInterrupt:
            if not self.__getShutDownRunning() :
                logger.info("WARNING! Ctrl+C received! shuttting down")
                self.__shutdown()
            else:
                logger.info("WARNING! Ctrl+C received! Shutdown signal ignored -- Another shutdown process running.")
        except AgentCritical, e:
            logger.critical(e)
            if not self.__getShutDownRunning():
                self.__shutdown()
                logger.info("WARNING! Exception captured, shutting down... !")
            else:
                logger.info("WARNING! Exception captured! Shutdown signal ignored -- Another shutdown process running")
        except Exception, e:
            logger.error("Unexpected exception: " + str(e))
            # print trace exception
            import traceback
            traceback.print_exc()
            # print to error.log too
            if self.conf.has_option("log", "error"):
                fd = open(self.conf.get("log", "error"), 'a+')
                traceback.print_exc(file=fd)
                fd.close()


if __name__ == "__main__":
#    sys.setcheckinterval(-1)
    a = Agent()
    a.main()
    print "Bye!"
    pid = os.getpid()
    os.kill(pid, signal.SIGKILL)

# vim:ts=4 sts=4 tw=79 expandtab
