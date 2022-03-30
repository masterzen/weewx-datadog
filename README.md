# weewx-datadog 

This is a weewx extension that sends observations as Datadog metrics.

Copyright 2022 Brice Figureau
Distributed under terms of the MIT License

## Installation

1) download

```shell
wget -O weewx-datadog.zip https://github.com/masterzen/weewx-datadog/archive/master.zip
```

2) run the installer:

```shell
wee_extension --install weewx-datadog.zip
```

3) enter parameters in weewx.conf:

```ini
[StdRESTful]
    [[Datadog]]
        api_key = <your API Key>
        app_key = <your app key>
```

4) restart weewx:

```shell
sudo /etc/init.d/weewx stop
sudo /etc/init.d/weewx start
```

## Configuration

A minimal configuration requires only an `api_key` and an `app_key`.

Here is a complete enumeration of options.  Specify only those that you need.

```ini
[StdRESTful]
    [[Datadog]]
        api_key = 03423...
        app_key = abcd...
        binding = loop,archive                 # default is archive
        station_name = ...                     # optional, sets the datadog host nane
        api_host = (https://api.datadoghq.com|https://api.datadoghq.eu) # optional specify datadog host
        tags = location:A,field:C              # optional
