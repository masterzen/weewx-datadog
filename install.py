# installer for datadog reporter
# Copyright 2021 Brice Figureau
# Distributed under the terms of the GNU Public License (GPLv3)

from weecfg.extension import ExtensionInstaller

def loader():
    return DatadogInstaller()

class DatadogInstaller(ExtensionInstaller):
    def __init__(self):
        super(DatadogInstaller, self).__init__(
            version="0.0.1",
            name='datadog_uploader',
            description='send weather data as datadog metrics.',
            author="Brice Figureau",
            author_email="",
            restful_services='user.datadog_uploader.Datadog',
            config={
                'StdRESTful': {
                    'Datadog': {
                        'api_key': 'INSERT_API_KEY_HERE',
                        'app_key': 'INSERT_APP_KEY_HERE'}}},
            files=[('bin/user', ['bin/user/datadog_uploader.py'])]
            )
