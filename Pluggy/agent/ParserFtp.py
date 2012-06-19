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
import os, sys, time, re, socket
from time import sleep
from time import time
import pdb
import pyinotify #deb package python-pyinotify
# python-pyinotify version 0.7.1-1
from pyinotify import WatchManager, Notifier, ThreadedNotifier, EventsCodes, ProcessEvent
from threading import Lock
#
# LOCAL IMPORTS
#
from Profiler import TimeProfiler
from Detector import Detector
from Event import Event, EventOS, EventMac, EventService, EventHids, EventIdm
from Logger import Logger
from TailFollow import TailFollow
from TailFollowBookmark import TailFollowBookmark
import glob
from ParserLog import RuleMatch
from ftplib import FTP
logger = Logger.logger

class ParserFTP(Detector):

    def __init__(self, conf, plugin,queue = None):
        self._conf = conf        # config.cfg info
        self._plugin = plugin    # plugins/X.cfg info
        self.rules = []          # list of RuleMatch objects
        self.stop_processing = False
        self.queue = queue
        self.idmConnected = False
        Detector.__init__(self, conf, plugin)


    def loadRules(self):
        unsorted_rules = self._plugin.rules()
        keys = unsorted_rules.keys()
        keys.sort()
        for key in keys:
            item = unsorted_rules[key]
            self.rules.append(RuleMatch(key, item, self._plugin))

    def tryMatchRule(self,line):
        rule_matched = False
        for rule in self.rules:
            rule.feed(line)
            if rule.match() and not rule_matched:
                logger.debug('Match rule: [%s] -> %s' % (rule.name, line))
                event = rule.generate_event()
                self.resetAllrules()
                # send the event as appropriate
                if event is not None:
                    self.send_message(event)
                # one rule matched, no need to check more
                rule_matched = True
                break
    def resetAllrules(self):
        for rule in self.rules:
            rule.resetRule()
    def process(self):
        self.loadRules()
        ftp_host = self._plugin.get("config", "ftp_server")
        ftp_port = self._plugin.get("config", "ftp_port")
        ftp_user = self._plugin.get("config", "ftp_user")
        ftp_pswd = self._plugin.get("config", "ftp_user_password")
        sleep_time = self._plugin.get("config", "tSleep")
        remote_file =  self._plugin.get("config", "remote_filename")
        start_time = time()
        mustSend = False
        while not self.stop_processing:
            timenow_time=time()
            #connecto to ftp server and retreive the file
            if self.queue is not None and not self.queue.empty():
                msg = self.queue.get_nowait()
                logger.debug("--------->>>>> Messsage queue: %s" % msg)
                if msg is not None:
                    if msg.rstrip().startswith('idmconnected'):
                        self.idmConnected = True
                    elif msg.rstrip().startswith('sendme'):
                        mustSend = True
            elapsed_time = timenow_time - start_time
            
            if float(elapsed_time) < float(sleep_time):
                sleep(1)
                if not mustSend:
                    continue
                else:
                    mustSend = False
                    start_time = time()
            else:
                start_time = time()
            if not self.idmConnected:
                continue
            try:
                ftp_conn = FTP(ftp_host)
                filename = "/tmp/file_%s"% time()
                file_tmp = open(filename,'wb')
                ftp_conn.login(ftp_user,ftp_pswd)
                cmd = "RETR " + remote_file
                logger.info("FTP cmd:%s" %cmd)
                ftp_conn.retrbinary(cmd,file_tmp.write)
                file_tmp.close()
                #read the file
                file_tmp = open(filename)
                for line in file_tmp:
                    self.tryMatchRule(line)
                    sleep(0.001)
                file_tmp.close()
                os.remove(filename)
                start_time = time()
            except Exception,e:
                logger.error("Can't connect to ftp hosts: %s, error: %s" % (ftp_host,str(e)))
            
            #sleep(float(sleep_time))
    def stop(self):
        logger.debug("Scheduling stop of ParserFTP.")
        self.stop_processing = True
        try:
            self.join(1)
        except RuntimeError:
            logger.warning("Stopping thread that likely hasn't started.")
