#!/usr/bin/env python
#future comptability
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from builtins import (ascii, bytes, chr, dict, filter, hex, input,
                      int, map, next, oct, open, pow, range, round,
                      str, super, zip)

import os
from datetime import datetime
import time
from tzlocal import get_localzone
import sys
from queue import Queue
import threading

from configparser import ConfigParser

from plcclient import PlcClient
from database import Database

class TagReader(threading.Thread):
    def __init__(self, client, logCycle, tags, lock, queue):
        super().__init__()
        self._logCycle = logCycle
        self._client = client
        self._tags = tags
        self._queue = queue
        self._lock = lock
        logger.info('{} started. (cycle {})'.format(self.getName(), self._logCycle))
        self.start()
        
    def run(self):
        while True:
            with self._lock:
                logger.debug('{} - acquired lock'.format(self.getName()))
                if self._client.get_connected():
                    logger.debug('{} - client attempting to read tag/s'.format(self.getName()))
                    response = self._client.tag_read(self._tags)
                    if response is None:
                        logger.warning('{} - response from tag_read() was None'.format(self.getName()))
                        self._client.reset_connection()     #blocking call, returns when connection re-established
                        response = self._client.tag_read(self._tags)
                    self._queue.put(response)
            time.sleep(self._logCycle)

#generator function that returns position of set bits in n
def bit_test(n):
    while n:
        b = n & (~n+1)
        yield b
        n ^= b

def main():
    logger.info('Start')

    #instantiate the db
    dbFile = 'logs.db'
    dbTable = 'alarm_log'
    dbColumns = (('Procedure', 'INTEGER'), ('Class', 'INTEGER'), ('State', 'INTEGER'), ('Description', 'TEXT'), ('Time', 'TEXT'), ('TZ', 'TEXT')) 
    db = Database(dbFile)
    
    #TODO got to fix this!! need to implement the creation script (lookup flask documentation for example)
#     if not db.table:
#         db.create(dbTable, dbColumns)
    
    #clear the table if desired
    clearTable = sys.argv[1] if len(sys.argv) == 2 else None
    if clearTable == 'clear': 
        db.cursor.execute('DELETE FROM {}'.format(dbTable))
        db.connection.commit()
            
    #instantiate plc client and connect
    plcClient = PlcClient('alarm_tags.cfg', 'plc.cfg')
    plcClient.connect()
    
    #parse alarm definitions config file
    alarmDefinition = ConfigParser()
    alarmDefinition.read('alarm_definitions.cfg')

    #fire off pool of threads that grab tags from plc at different logging cycles
    threadLock = threading.Lock()
    queue = Queue()
    for cycle in plcClient.cycles:
        TagReader(plcClient, cycle, plcClient.tagsByCycle[cycle], threadLock, queue)
 
    #do some work on collected tags
    onChangeLogsWere = {}
    tzid = get_localzone().zone
    while True:
        if queue.not_empty:
            dequeue = queue.get()
            
            #all tags defined for alarms should be type "on-change".  to be safe, explicitly filter for on_change anyway.
            onChangeLogs = {key:dequeue[key] for key in dequeue.keys() if key in plcClient.tagsByAcqMode['on_change']}
            timestamp = datetime.utcnow().replace(microsecond=0).isoformat()
            
            #on first pass, onChangeLogsWere will be empty.  copy onChangeLogs into it with bit-wise inverted values
            if not onChangeLogsWere:
                onChangeLogsWere = {tag:~val for tag, val in onChangeLogs.items()}

            #test for changes 
            if onChangeLogs and cmp(onChangeLogs, onChangeLogsWere):
                logger.info('On-change log/s detected; Inserting into DB')
                for tag, val in onChangeLogs.items():
                    if (onChangeLogs[tag] != onChangeLogsWere[tag]):
                        #determine which bits of the alarm word have changed
                        changedBits = onChangeLogs[tag] ^ onChangeLogsWere[tag]
                        print(tag, 'changed bits:', bin(changedBits))
                        if changedBits > 0:
                            for position in bit_test(changedBits):
                                #if there are changed bits, 
                                print('position value {}, position {}'.format(position, position.bit_length()))
                                description = alarmDefinition[tag][str(int(position).bit_length())]
                                state = int(bool(changedBits & onChangeLogs[tag]))
                                _class = alarmDefinition[tag].getint('class')
                                print('Change Detected! {} = {}    Desc: {} = {}   {}   {}   class {}'.format(tag, val, description, state, timestamp, tzid, _class))
                                db.insert(dbTable, (2, _class, state, description, timestamp, tzid))
                                
                #cache a copy tags for comparison next scan
                onChangeLogsWere = onChangeLogs
         
    
if __name__ == "__main__": 
    import logging
    import logging.handlers
    
    #create local logger
    logger = logging.getLogger(__name__)

    #create a rotating file logging handler and formatter
    handler = logging.handlers.RotatingFileHandler('alarmlogger.log', maxBytes=500000000, backupCount=1)
    formatter = logging.Formatter(fmt='%(asctime)s %(name) -15s %(levelname)-9s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    handler.setFormatter(formatter)
    
    #create a logging whitelist - (comment out code in ~~ block to enable all child loggers)
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#     loggingWhitelist = ('root', 'plcclient', 'snap7', 'database', '__main__')
#     class Whitelist(logging.Filter):
#         def __init__(self, *whitelist):
#             self.whitelist = [logging.Filter(name) for name in whitelist]
#     
#         def filter(self, record):
#             return any(f.filter(record) for f in self.whitelist)
#     #add the whitelist filter to the handler
#     handler.addFilter(Whitelist(*loggingWhitelist))
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    #assign the handler to root logger (we use the root logger so that we get output from all child logger used in other modules)
    logging.root.addHandler(handler)
    #set the logging level for root logger
    logging.root.setLevel(logging.DEBUG)
    main()