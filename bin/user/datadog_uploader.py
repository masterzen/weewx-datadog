# Copyright 2021 Brice Figureau
# Distributed under the terms of the GNU Public License (GPLv3)

"""
This is a weewx extension that uploads data to DataDog through the datadog API.

Minimal Configuration

An API KEY and APP KEY are required.  All weewx metrics will be uploaded using weewx
names.

[StdRESTful]
    [[Datadog]]
        api_key = ...
        app_key = ...
        station_name = ...

"""
import numbers
import queue
import base64
import re
import sys
from distutils.version import StrictVersion
import http.client as http_client

from datadog import initialize, api, statsd

import weewx
import weewx.restx
import weewx.units
from weeutil.weeutil import to_bool, accumulateLeaves

VERSION = "0.0.1"

REQUIRED_WEEWX = "3.5.0"
if StrictVersion(weewx.__version__) < StrictVersion(REQUIRED_WEEWX):
    raise weewx.UnsupportedFeature("weewx %s or greater is required, found %s"
                                   % (REQUIRED_WEEWX, weewx.__version__))

try:
    # Test for new-style weewx logging by trying to import weeutil.logger
    import weeutil.logger
    import logging

    log = logging.getLogger(__name__)


    def logdbg(msg):
        log.debug(msg)


    def loginf(msg):
        log.info(msg)


    def logerr(msg):
        log.error(msg)

except ImportError:
    # Old-style weewx logging
    import syslog


    def logmsg(level, msg):
        syslog.syslog(level, 'restx: datadog: %s:' % msg)


    def logdbg(msg):
        logmsg(syslog.LOG_DEBUG, msg)


    def loginf(msg):
        logmsg(syslog.LOG_INFO, msg)


    def logerr(msg):
        logmsg(syslog.LOG_ERR, msg)

# observations that should be skipped when obs_to_upload is 'most'
OBS_TO_SKIP = ['dateTime', 'interval', 'usUnits']

MAX_SIZE = 1000000


class Datadog(weewx.restx.StdRESTbase):
    def __init__(self, engine, cfg_dict):
        """This service recognizes standard restful options plus the following:

        Required parameters:

        Optional parameters:

        binding: options include "loop", "archive", or "loop,archive"
        Default is archive
        """
        super(Datadog, self).__init__(engine, cfg_dict)
        loginf("service version is %s" % VERSION)
        site_dict = weewx.restx.get_site_dict(cfg_dict, 'Datadog', 'api_key', 'app_key',
                                              'station_name')
        if site_dict is None:
            return

        _manager_dict = weewx.manager.get_manager_dict_from_config(
            cfg_dict, 'wx_binding')
        site_dict['manager_dict'] = _manager_dict
        site_dict.setdefault('latitude', self.engine.stn_info.latitude_f)
        site_dict.setdefault('longitude', self.engine.stn_info.longitude_f)
        site_dict.setdefault('station_type', self.config_dict['Station'].get(
            'station_type', 'unknown'))
        site_dict.setdefault('altitude', self.engine.stn_info.altitude_vt.value)

        if 'tags' in site_dict:
            if isinstance(site_dict['tags'], list):
                site_dict['tags'] = ','.join(site_dict['tags'])
            loginf("tags %s" % site_dict['tags'])

        # we can bind to loop packets and/or archive records
        binding = site_dict.pop('binding', 'archive')
        if isinstance(binding, list):
            binding = ','.join(binding)
        loginf('binding is %s' % binding)

        data_queue = queue.Queue()
        try:
            data_thread = DatadogThread(data_queue, **site_dict)
        except weewx.ViolatedPrecondition as e:
            loginf("Data will not be posted: %s" % e)
            return
        data_thread.start()

        if 'loop' in binding.lower():
            self.loop_queue = data_queue
            self.loop_thread = data_thread
            self.bind(weewx.NEW_LOOP_PACKET, self.new_loop_packet)
        if 'archive' in binding.lower():
            self.archive_queue = data_queue
            self.archive_thread = data_thread
            self.bind(weewx.NEW_ARCHIVE_RECORD, self.new_archive_record)

    def new_loop_packet(self, event):
        data = {'binding': 'loop'}
        data.update(event.packet)
        self.loop_queue.put(data)

    def new_archive_record(self, event):
        data = {'binding': 'archive'}
        data.update(event.record)
        self.archive_queue.put(data)


class DatadogThread(weewx.restx.RESTThread):
    """
    Thread for sending WeeWX Weather Data to datadog.
    """

    DEFAULT_PREFIX = 'weewx'
    DEFAULT_POST_INTERVAL = 10
    DEFAULT_TIMEOUT = 60
    DEFAULT_MAX_TRIES = 3
    DEFAULT_RETRY_WAIT = 5
    DEFAULT_TAGS = []
    CAMEL_CASE_PATTERN = re.compile(r'(?<!^)(?=[A-Z])')

    def __init__(self, queue, manager_dict,
                 api_key, app_key, station_name, api_host=None, tags=None, prefix=DEFAULT_PREFIX,
                 latitude=None, longitude=None, station_type=None, altitude=0,
                 skip_upload=False, post_interval=DEFAULT_POST_INTERVAL,
                 max_backlog=sys.maxsize, stale=None, log_success=True,
                 log_failure=True, timeout=DEFAULT_TIMEOUT,
                 max_tries=DEFAULT_MAX_TRIES, retry_wait=DEFAULT_RETRY_WAIT):
        """Initialize an instances of DatadogThread.
        :param api_key: datadog api key.
        :param app_key: datadog app key.
        :param prefix: Graphite Queue Prefix.
        :param log_success: Log a successful post in the system log.
        :param log_failure: Log an unsuccessful post in the system log.
        :param max_backlog: Max length of Queue before trimming. dft=sys.maxint
        :param max_tries: How many times to try the post before giving up.
        :param stale: How old a record can be and still considered useful.
        :param post_interval: The interval in seconds between posts.
        :param timeout: How long to wait for the server to respond before fail.
        :param skip_upload: Debugging option to display data but do not upload.
        """
        super(DatadogThread, self).__init__(
            queue,
            protocol_name='Datadog',
            manager_dict=manager_dict,
            post_interval=post_interval,
            max_backlog=max_backlog,
            stale=stale,
            log_success=log_success,
            log_failure=log_failure,
            timeout=timeout,
            max_tries=max_tries,
            retry_wait=retry_wait
        )

        if tags is None:
            tags = self.DEFAULT_TAGS

        self.prefix = prefix
        self.tags = tags
        self.skip_upload = weeutil.weeutil.to_bool(skip_upload)

        if latitude:
            self.tags.append("latitude:%s" % latitude)
        if longitude:
            self.tags.append("longitude:%s" % longitude)
        if station_type:
            self.tags.append("station_type:%s" % station_type)
        if station_type:
            self.tags.append("altitude:%s" % altitude)

        options = {
            "api_key": api_key,
            "app_key": app_key,
            "host_name": station_name,
            "api_host": api_host,
        }

        initialize(**options)

    def collect_metric(self, record):
        metrics = list()
        for key, value in record.items():
            _key = self.CAMEL_CASE_PATTERN.sub('_', key).lower()

            if self.prefix:
                metric_name = '.'.join([self.prefix, _key])
            else:
                metric_name = _key

            if value is None:
                _value = 0.0
            else:
                _value = value

            if not isinstance(value, numbers.Number):
                continue

            metrics.append(
                {'metric': metric_name, 'type': 'gauge', 'points': (record['dateTime'], _value)})

        result = api.metrics.Metric.send(metrics=metrics, tags=self.tags)

    def process_record(self, record, dbmanager):

        if self.skip_upload:
            syslog.syslog(
                syslog.LOG_DEBUG,
                "datadog_uploader: skip_upload=True, skipping upload"
            )
        else:
            # Get the full record by querying the database ...
            _full_record = self.get_record(record, dbmanager)
            self.collect_metric(_full_record)


# Use this hook to test the uploader:
#   PYTHONPATH=bin python bin/user/datadog_uploader.py

if __name__ == "__main__":
    import optparse
    import time

    weewx.debug = 2

    usage = """Usage: python -m datadog_uploader --help
       python -m datadog_uploader --version
       python -m datadog_uploader [--api-key=API_KEY] 
                        [--app_key=APP_KEY][--host_name=HOST][--measurement=MEASUREMENT]
                        [--tags=TAGS]"""

    parser = optparse.OptionParser(usage=usage)
    parser.add_option('--version', action='store_true',
                      help='Display weewx-influx version')
    parser.add_option('--api-key', default='',
                      help="Datadog API KEy",
                      metavar="API-KEY")
    parser.add_option('--app-key', default='',
                      help="Datadog APP KEY",
                      metavar="APP-KEY")
    parser.add_option('--host-name', default='weewx',
                      help="Weatherstation name",
                      metavar="NAME")
    parser.add_option('--measurement', default='record',
                      help="InfluxDB measurement name. Default is 'record'",
                      metavar="MEASUREMENT")
    parser.add_option('--tags', default='station=A,field=C',
                      help="Datadog tags to be used. Default is 'station=A,field=C'",
                      metavar="TAGS")
    (options, args) = parser.parse_args()

    if options.version:
        print("weewx-influxdb version %s" % VERSION)
        exit(0)

    print("Using server-url of '%s'" % options.server_url)

    queue = queue.Queue()
    t = DatadogThread(queue,
                      manager_dict=None,
                      api_key=options.api_key,
                      app_key=options.app_key,
                      host_name=options.host_name,
                      tags=options.tags)
    queue.put({'dateTime': int(time.time() + 0.5),
               'usUnits': weewx.US,
               'outTemp': 32.5,
               'inTemp': 75.8,
               'outHumidity': 24})
    queue.put(None)
    t.run()
