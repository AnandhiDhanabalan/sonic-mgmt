from tabulate import tabulate
from tests.common.utilities import (wait, wait_until)
from tests.common.helpers.assertions import pytest_assert
from tests.common.reboot import reboot
from threading import Thread
import json
import ipaddr
import time
import logging

logger = logging.getLogger(__name__)

TGEN_AS_NUM = 65200
DUT_AS_NUM = 65100
TIMEOUT = 30
BGP_TYPE = 'ebgp'
temp_tg_port = dict()
aspaths = [65002, 65003]
bgp_up_start_timer = 0
bgp_down_start_timer = 0
bgp_up_time = 0
loopback_up_start_timer = 0
loopback_down_start_timer = 0
loopback_up_time = 0


def run_reboot_test(snappi_api,
                    duthost,
                    localhost,
                    tgen_ports,
                    reboot_type,
                    ):
    """
    Run Local link failover test
    Args:
        snappi_api (pytest fixture): snappi API
        duthost (pytest fixture): duthost fixture
        localhost (pytest fixture): localhost handle
        tgen_ports (pytest fixture): Ports mapping info of T0 testbed
        reboot_type : Type of reboot
    """

    """ Create bgp config on dut """
    duthost_bgp_config(duthost, tgen_ports)

    """ Create bgp config on TGEN """
    tgen_bgp_config = __tgen_bgp_config(snappi_api)

    """
        Run the convergence test by flapping all the rx
        links one by one and calculate the convergence valuess
    """
    get_convergence_for_reboot_test(duthost, localhost, snappi_api,
                                    tgen_bgp_config, reboot_type, )


def duthost_bgp_config(duthost, tgen_ports):
    """
    Configures BGP on the DUT with N-1 ecmp
    Args:
        duthost (pytest fixture): duthost fixture
        tgen_ports (pytest fixture): Ports mapping info of T0 testbed
    """
    global temp_tg_port
    temp_tg_port = tgen_ports
    for i in range(1, 4):
        intf_config = (
            "sudo config interface ip remove %s %s/%s \n"
            "sudo config interface ip remove %s %s/%s \n"
        )
        intf_config %= (tgen_ports[i]['peer_port'], tgen_ports[i]['peer_ip'],
                        tgen_ports[i]['prefix'],
                        tgen_ports[i]['peer_port'],
                        tgen_ports[i]['peer_ipv6'],
                        tgen_ports[i]['ipv6_prefix'])
        logger.info('Removing configured IP and IPv6 Address from %s' %
                    (tgen_ports[i]['peer_port']))
        duthost.shell(intf_config)
    vlan_config = (
        'sudo config vlan add 1000\n'
        'sudo config vlan member add -u 1000 %s\n'
        'sudo config vlan member add -u 1000 %s\n'
        'sudo config interface ip add Vlan1000 192.168.1.1/16\n'
        'sudo config interface ip add Vlan1000 5001::1/64\n'
    )
    vlan_config %= (tgen_ports[3]['peer_port'], tgen_ports[2]['peer_port'])
    logger.info('Adding %s and %s to Vlan 1000' %
                (tgen_ports[3]['peer_port'], tgen_ports[2]['peer_port']))
    duthost.shell(vlan_config)
    portchannel_config = (
        "sudo config portchannel add PortChannel1 \n"
        "sudo config portchannel member add PortChannel1 %s\n"
        "sudo config interface ip add PortChannel1 %s/%s\n"
        "sudo config interface ip add PortChannel1 %s/%s\n"
    )
    portchannel_config %= (tgen_ports[1]['peer_port'],
                           tgen_ports[1]['peer_ip'],
                           tgen_ports[1]['prefix'],
                           tgen_ports[1]['peer_ipv6'], 64)
    logger.info('Configuring %s to PortChannel1' %
                (tgen_ports[1]['peer_port']))
    logger.info('Portchannel1 (IPv4,IPv6)  : ({},{})'.format(
        tgen_ports[1]['peer_ip'], tgen_ports[1]['peer_ipv6']))
    duthost.shell(portchannel_config)
    loopback = (
        "sudo config interface ip add Loopback0 1.1.1.1/32\n"
    )
    logger.info('Configuring 1.1.1.1/32 on the loopback interface')
    duthost.shell(loopback)
    logger.info('Saving Interface, Vlan and Portchannel configuration in config_db.json')
    duthost.command('sudo config save -y \n')

    logger.info('Configuring BGP in config_db.json')
    config_db = json.loads(duthost.shell("sonic-cfggen -d --print-data")['stdout'])
    bgp_neighbors = dict()
    device_neighbors = dict()
    device_neighbor_metadatas = dict()
    bgp_neighbor = \
        {
            tgen_ports[1]['ip']:
            {
                "asn": TGEN_AS_NUM,
                "holdtime": "180",
                "keepalive": "60",
                "local_addr": tgen_ports[1]['peer_ip'],
                "name": "snappi-sonic",
                "nhopself": "0",
                "rrclient": "0"
            },
            tgen_ports[1]['ipv6']:
            {
                "asn": TGEN_AS_NUM,
                "holdtime": "180",
                "keepalive": "60",
                "local_addr": tgen_ports[1]['peer_ipv6'],
                "name": "snappi-sonic",
                "nhopself": "0",
                "rrclient": "0"
            },
        }
    bgp_neighbors.update(bgp_neighbor)
    device_neighbor = {
                                'Ethernet0':
                                {
                                    "name": "snappi-sonic",
                                    "port": "Ethernet1"
                                }
                            }
    device_neighbors.update(device_neighbor)
    device_neighbor_metadata = {
                                    "snappi-sonic":
                                    {
                                        "hwsku": "Snappi",
                                        "mgmt_addr": "172.16.149.206",
                                        "type": "ToRRouter"
                                    }
                                }
    device_neighbor_metadatas.update(device_neighbor_metadata)
    if "BGP_NEIGHBOR" not in config_db.keys():
        config_db["BGP_NEIGHBOR"] = bgp_neighbors
    else:
        config_db["BGP_NEIGHBOR"].update(bgp_neighbors)

    if "DEVICE_NEIGHBOR" not in config_db.keys():
        config_db["DEVICE_NEIGHBOR"] = device_neighbors
    else:
        config_db["DEVICE_NEIGHBOR"].update(device_neighbors)

    if 'DEVICE_NEIGHBOR_METADATA' not in config_db.keys():
        config_db["DEVICE_NEIGHBOR_METADATA"] = device_neighbor_metadatas
    else:
        config_db["DEVICE_NEIGHBOR_METADATA"].update(device_neighbor_metadatas)

    with open("/tmp/temp_config.json", 'w') as fp:
        json.dump(config_db, fp, indent=4)
    duthost.copy(src="/tmp/temp_config.json", dest="/etc/sonic/config_db.json")
    logger.info('Reloading config_db.json to apply IP and BGP configuration on {}'.format(duthost.hostname))
    pytest_assert('Error' not in duthost.shell("sudo config reload /etc/sonic/config_db.json -f -y \n")['stderr'],
                  'Error while reloading config in {} !!!!!'.format(duthost.hostname))
    logger.info('Config Reload Successful in {} !!!'.format(duthost.hostname))


def get_flow_stats(snappi_api, name):
    """
    Args:
        snappi_api (pytest fixture): Snappi API
    """
    req = snappi_api.metrics_request()
    req.flow.flow_names = []
    return snappi_api.get_metrics(req).flow_metrics


def get_macs(mac, count, offset=1):
    """
    Take mac as start mac returns the count of macs in a list
    """
    mac_list = list()
    for i in range(count):
        mac_address = "{:012X}".format(int(mac, 16) + offset * i)
        mac_address = ":".join(
            format(s, "02x") for s in bytearray.fromhex(mac_address)
        )
        mac_list.append(mac_address)
    return mac_list


def get_ip_addresses(ip, count, type='ipv4'):
    """
    Take ip as start ip returns the count of ips in a list
    """
    ip_list = list()
    for i in range(count):
        if type == 'ipv6':
            ipaddress = ipaddr.IPv6Address(ip)
        else:
            ipaddress = ipaddr.IPv4Address(ip)
        ipaddress = ipaddress + i
        value = ipaddress._string_from_ip_int(ipaddress._ip)
        ip_list.append(value)
    return ip_list


def __tgen_bgp_config(snappi_api, ):
    """
    Creating  BGP config on TGEN
    Args:
        snappi_api (pytest fixture): snappi API
    """
    config = snappi_api.config()
    snappi_api.enable_scaling(True)

    p1, p2, p3 = (
        config.ports.port(name="t1", location=temp_tg_port[1]['location'])
        .port(name="server2", location=temp_tg_port[2]['location'])
        .port(name="server1", location=temp_tg_port[3]['location'])
    )
    lag3 = config.lags.lag(name="lag1")[-1]
    lp3 = lag3.ports.port(port_name=p1.name)[-1]
    lag3.protocol.lacp.actor_system_id = "00:11:03:00:00:03"
    lp3.ethernet.name = "lag_Ethernet 3"
    lp3.ethernet.mac = "00:13:01:00:00:01"

    config.options.port_options.location_preemption = True
    layer1 = config.layer1.layer1()[-1]
    layer1.name = 'port settings'
    layer1.port_names = [port.name for port in config.ports]
    layer1.ieee_media_defaults = False
    layer1.auto_negotiation.rs_fec = False
    layer1.auto_negotiation.link_training = False
    layer1.speed = temp_tg_port[1]['speed']
    layer1.auto_negotiate = False

    conf_values = dict()
    num_of_devices = 1000
    conf_values['server_1_ipv4'] = get_ip_addresses("192.168.1.2", 2000)[::2]
    conf_values['server_2_ipv4'] = get_ip_addresses("192.168.1.2", 2000)[1::2]
    conf_values['server_1_ipv6'] = get_ip_addresses("5000::2", 2000,
                                                    'ipv6')[::2]
    conf_values['server_2_ipv6'] = get_ip_addresses("5000::2", 2000,
                                                    'ipv6')[1::2]
    conf_values['server_1_mac'] = get_macs("001700000011", num_of_devices)
    conf_values['server_2_mac'] = get_macs("001600000011", num_of_devices)
    for i in range(1, num_of_devices + 1):
        # server1
        d1 = config.devices.device(name='Server_1_{}'.format(i - 1))[-1]
        eth_1 = d1.ethernets.add()
        eth_1.connection.port_name = p3.name
        eth_1.name = 'Ethernet 1_{}'.format(i - 1)
        eth_1.mac = conf_values['server_1_mac'][i - 1]
        ipv4_1 = eth_1.ipv4_addresses.add()
        ipv4_1.name = 'IPv4 1_{}'.format(i - 1)
        ipv4_1.address = conf_values['server_1_ipv4'][i - 1]
        ipv4_1.gateway = '192.168.1.1'
        ipv4_1.prefix = 16
        ipv6_1 = eth_1.ipv6_addresses.add()
        ipv6_1.name = 'IPv6 1_{}'.format(i - 1)
        ipv6_1.address = conf_values['server_1_ipv6'][i - 1]
        ipv6_1.gateway = '5001::1'
        ipv6_1.prefix = 128
        # server2
        d2 = config.devices.device(name='Server_2_{}'.format(i - 1))[-1]
        eth_2 = d2.ethernets.add()
        eth_2.connection.port_name = p2.name
        eth_2.name = 'Ethernet 2_{}'.format(i - 1)
        eth_2.mac = conf_values['server_2_mac'][i - 1]
        ipv4_2 = eth_2.ipv4_addresses.add()
        ipv4_2.name = 'IPv4 2_{}'.format(i - 1)
        ipv4_2.address = conf_values['server_2_ipv4'][i - 1]
        ipv4_2.gateway = '192.168.1.1'
        ipv4_2.prefix = 16
        ipv6_2 = eth_2.ipv6_addresses.add()
        ipv6_2.name = 'IPv6 2_{}'.format(i - 1)
        ipv6_2.address = conf_values['server_2_ipv6'][i - 1]
        ipv6_2.gateway = '5001::1'
        ipv6_2.prefix = 128

    # T1
    d3 = config.devices.device(name="T1")[-1]
    eth_3 = d3.ethernets.add()
    eth_3.connection.port_name = lag3.name
    eth_3.name = 'Ethernet 3'
    eth_3.mac = "00:14:01:00:00:01"
    ipv4_3 = eth_3.ipv4_addresses.add()
    ipv4_3.name = 'IPv4 3'
    ipv4_3.address = temp_tg_port[1]['ip']
    ipv4_3.gateway = temp_tg_port[1]['peer_ip']
    ipv4_3.prefix = 24
    ipv6_3 = eth_3.ipv6_addresses.add()
    ipv6_3.name = 'IPv6 3'
    ipv6_3.address = temp_tg_port[1]['ipv6']
    ipv6_3.gateway = temp_tg_port[1]['peer_ipv6']
    ipv6_3.prefix = 128

    bgpv4_stack = d3.bgp
    bgpv4_stack.router_id = temp_tg_port[1]['peer_ip']
    bgpv4_int = bgpv4_stack.ipv4_interfaces.add()
    bgpv4_int.ipv4_name = ipv4_3.name
    bgpv4_peer = bgpv4_int.peers.add()
    bgpv4_peer.name = 'BGP 3'
    bgpv4_peer.as_type = BGP_TYPE
    bgpv4_peer.peer_address = temp_tg_port[1]['peer_ip']
    bgpv4_peer.as_number = int(TGEN_AS_NUM)
    route_range1 = bgpv4_peer.v4_routes.add(name="Network Group 1")
    route_range1.addresses.add(address='200.1.0.1', prefix=32, count=4000)
    as_path = route_range1.as_path
    as_path_segment = as_path.segments.add()
    as_path_segment.type = as_path_segment.AS_SEQ
    as_path_segment.as_numbers = aspaths

    bgpv6_stack = d3.bgp
    bgpv6_stack.router_id = temp_tg_port[1]['peer_ip']
    bgpv6_int = bgpv6_stack.ipv6_interfaces.add()
    bgpv6_int.ipv6_name = ipv6_3.name
    bgpv6_peer = bgpv6_int.peers.add()
    bgpv6_peer.name = 'BGP+ 3'
    bgpv6_peer.as_type = BGP_TYPE
    bgpv6_peer.peer_address = temp_tg_port[1]['peer_ipv6']
    bgpv6_peer.as_number = int(TGEN_AS_NUM)
    route_range2 = bgpv4_peer.v6_routes.add(name="Network Group 2")
    route_range2.addresses.add(address='3000::1', prefix=128, count=3000)
    as_path = route_range2.as_path
    as_path_segment = as_path.segments.add()
    as_path_segment.type = as_path_segment.AS_SEQ
    as_path_segment.as_numbers = aspaths

    def createTrafficItem(traffic_name, src, dest, rate=50):
        flow1 = config.flows.flow(name=str(traffic_name))[-1]
        flow1.tx_rx.device.tx_names = src
        flow1.tx_rx.device.rx_names = dest
        flow1.size.fixed = 1024
        flow1.rate.percentage = rate
        flow1.metrics.enable = True

    ipv4_1_names = ["IPv4 1_{}".format(i - 1) for i in range(1,
                                                             num_of_devices +
                                                             1)]
    ipv4_2_names = ["IPv4 2_{}".format(i - 1) for i in range(1,
                                                             num_of_devices +
                                                             1)]
    ipv6_1_names = ["IPv6 1_{}".format(i - 1) for i in range(1,
                                                             num_of_devices +
                                                             1)]
    ipv6_2_names = ["IPv6 2_{}".format(i - 1) for i in range(1,
                                                             num_of_devices +
                                                             1)]
    createTrafficItem("IPv4_1-IPv4_2", ipv4_1_names, ipv4_2_names)
    createTrafficItem("IPv6_2-IPv6_1", ipv6_2_names, ipv6_1_names)
    createTrafficItem("IPv4_1-T1", ipv4_1_names, [route_range1.name])
    createTrafficItem("IPv6_2-T1", ipv6_2_names, [route_range2.name])
    createTrafficItem("T1-IPv4_1", [route_range1.name], ipv4_1_names)
    createTrafficItem("T1-IPv6_2", [route_range2.name], ipv6_2_names)
    return config


def ping_loopback_if(snappi_api, ping_req):
    """
    Args:
        snappi_api (pytest fixture): snappi API
        ping_req : ping_req, snappi API object

    """
    return snappi_api.set_control_action(ping_req).response.protocol.ipv4.ping.responses


def get_bgpv4_metrics(snappi_api, bgp_req):
    """
    Args:
        snappi_api (pytest fixture): snappi API
        bgp_req : ping_req, snappi API object

    """
    return snappi_api.get_metrics(bgp_req).bgpv4_metrics


def wait_for_bgp_and_lb_soft(snappi_api, ping_req, ):
    """
    Method for when reboot type is Soft.  Check for Loopback I/F to go
    down then take timestamp.Then check for LoopBack I/F state to change
    from down to up and record timestamp.

    Args:
        snappi_api (pytest fixture): snappi API
        ping_req : ping_req, snappi API

    """
    global loopback_down_start_timer
    global loopback_up_start_timer

    found_lb_state = False
    while True:
        responses = ping_loopback_if(snappi_api, ping_req)
        if not found_lb_state and not responses[-1].result in "success":
            loopback_down_start_timer = time.time()
            found_lb_state = True
            logger.info('!!!!!!! 1. loopback timer started {} !!!!!!'.format(
                loopback_down_start_timer))
            break

    # reset states, look for BGP and Loopback states to come back up and mark
    # time
    found_lb_state = False
    while True:
        responses = ping_loopback_if(snappi_api, ping_req)
        if not found_lb_state and responses[-1].result in "success":
            loopback_up_start_timer = time.time()
            # found_lb_state = True
            logger.info('\n Ping Successfull \n!!!!!!! 2. loopback up end time {} !!!!!!'.format(
                loopback_up_start_timer))
            break


def wait_for_bgp_and_lb(snappi_api, ping_req, ):
    """
    Method to wait for BGP and Loopback state to change from up to down
    take timestamp of event. Then wait for BGP and Loopback state to
    change from down to up and take timestamp of event.

    Args:
        snappi_api (pytest fixture): snappi API
        ping_req : ping_req, snappi API
    """
    global loopback_down_start_timer
    global loopback_up_start_timer
    global bgp_down_start_timer
    global bgp_up_start_timer

    bgp_req = snappi_api.metrics_request()
    bgp_req.bgpv4.peer_names = []

    found_bgp_state = False
    found_lb_state = False
    while True:
        bgpv4_metrics = get_bgpv4_metrics(snappi_api, bgp_req)
        responses = ping_loopback_if(snappi_api, ping_req)
        if not found_bgp_state and bgpv4_metrics[-1].session_state in "down":
            bgp_down_start_timer = time.time()
            found_bgp_state = True
            logger.info('!!! 1. bgp is down time started {} !!!'.format(
                bgp_down_start_timer))
        if not found_lb_state and not responses[-1].result in "success":
            loopback_down_start_timer = time.time()
            found_lb_state = True
            logger.info('!!! 1. loopback timer started {} !!!'.format(
                loopback_down_start_timer))
        if bgpv4_metrics[-1].session_state in "down" and not \
                responses[-1].result in "success" and found_bgp_state and \
                found_lb_state:
            logger.info('BGP Control And LoopBack I/F Down')
            break

    # reset states, look for BGP and Loopback states to come back up and
    # mark time
    found_bgp_state = False
    found_lb_state = False
    while True:
        bgpv4_metrics = get_bgpv4_metrics(snappi_api, bgp_req)
        responses = ping_loopback_if(snappi_api, ping_req)
        if not found_bgp_state and bgpv4_metrics[-1].session_state in "up":
            bgp_up_start_timer = time.time()
            found_bgp_state = True
            logger.info(' ')
            logger.info('^^ 2. bgp is up end time {} ^^^'.format(
                bgp_up_start_timer))
        if not found_lb_state and responses[-1].result in "success":
            loopback_up_start_timer = time.time()
            found_lb_state = True
            logger.info(' ')
            logger.info('2. loopback up end time {} !!!'.format(
                loopback_up_start_timer))
        if bgpv4_metrics[-1].session_state in "up" and responses[-1].result \
                in "success" and found_bgp_state and found_lb_state:
            logger.info('BGP Control And LoopBack I/F Up')
            break


def get_convergence_for_reboot_test(duthost,
                                    localhost,
                                    snappi_api,
                                    bgp_config,
                                    reboot_type,
                                    ):
    """
    Args:
        duthost (pytest fixture): duthost fixture
        localhost (pytest fixture): localhost handle
        snappi_api (pytest fixture): snappi API
        bgp_config: __tgen_bgp_config
        reboot_type: Type of reboot
    """
    global bgp_up_start_timer
    global bgp_down_start_timer
    global loopback_up_start_timer
    global loopback_down_start_timer
    table, dp = [], []
    bgp_config.events.cp_events.enable = True
    bgp_config.events.dp_events.enable = True
    bgp_config.events.dp_events.rx_rate_threshold = 90
    snappi_api.set_config(bgp_config)
    flow_names = ["IPv4_1-IPv4_2", "IPv6_2-IPv6_1", "IPv4_1-T1",
                  "IPv6_2-T1", "T1-IPv4_1", "T1-IPv6_2"]
    logger.info('Starting Protocol')
    cs = snappi_api.control_state()
    cs.protocol.all.state = cs.protocol.all.START
    snappi_api.set_control_state(cs)
    wait(TIMEOUT, "For Protocols To start")
    logger.info('Starting Traffic')
    cs = snappi_api.control_state()
    cs.traffic.flow_transmit.state = cs.traffic.flow_transmit.START
    snappi_api.set_control_state(cs)
    wait(TIMEOUT, "For Traffic To start")

    def check_bgp_state():
        req = snappi_api.metrics_request()
        req.bgpv4.peer_names = []
        bgpv4_metrics = snappi_api.get_metrics(req).bgpv4_metrics
        assert bgpv4_metrics[-1].session_state == "up", \
            "BGP v4 Session State is not UP"
        logger.info("BGP v4 Session State is UP")
        req.bgpv6.peer_names = []
        bgpv6_metrics = snappi_api.get_metrics(req).bgpv6_metrics
        assert bgpv6_metrics[-1].session_state == "up", \
            "BGP v6 Session State is not UP"
        logger.info("BGP v6 Session State is UP")

    check_bgp_state()
    ping_req = snappi_api.control_action()
    ping_req.protocol.ipv4.ping.requests.add(src_name='IPv4 3', dst_ip="1.1.1.1")

    logger.info("Issuing a {} reboot on the dut {}".format(
        reboot_type, duthost.hostname))
    Thread(target=reboot, args=([duthost, localhost, reboot_type])).start()
    reboot_type_lists = ['warm', 'cold', 'fast']
    if reboot_type in reboot_type_lists:
        wait_for_bgp_and_lb(snappi_api, ping_req, )
    else:
        # soft-reboot
        wait_for_bgp_and_lb_soft(snappi_api, ping_req)
    bgp_up_time = bgp_up_start_timer - bgp_down_start_timer
    loopback_up_time = loopback_up_start_timer - loopback_down_start_timer
    logger.info("Wait until the system is stable")
    pytest_assert(wait_until(360, 10, 1,
                             duthost.critical_services_fully_started),
                  "Not all critical services are fully started")
    request = snappi_api.metrics_request()
    request.convergence.flow_names = []  # flow_names
    convergence_metrics = snappi_api.get_metrics(request).convergence_metrics
    for i, metrics in zip(flow_names, convergence_metrics):
        if reboot_type == "warm":
            request.flow.flow_names = [i]
            flow = snappi_api.get_metrics(request).flow_metrics
            if flow[0].frames_tx_rate != flow[0].frames_tx_rate:
                logger.info("Some Loss Observed in Traffic Item {}".format(i))
                dp.append(metrics.data_plane_convergence_us / 1000)
                logger.info(
                    'DP/DP Convergence Time (ms) of {} : {}'
                    .format(i, metrics.data_plane_convergence_us / 1000))
            else:
                dp.append(0)
                logger.info('DP/DP Convergence Time (ms) of {} : '
                            '{}'.format(i, 0))
        else:
            request.flow.flow_names = [i]
            flow = snappi_api.get_metrics(request).flow_metric
            assert int(flow[0].frames_tx_rate) != 0, \
                "No Frames sent for traffic item: {}".format(i)
            assert flow[0].frames_tx_rate == flow[0].frames_tx_rate, \
                "Loss observed for Traffic Item: {}".format(i)
            logger.info("No Loss Observed in Traffic Item {}".format(i))
            dp.append(metrics.data_plane_convergence_us / 1000)
            logger.info('DP/DP Convergence Time (ms) of {} : {}'.
                        format(i, metrics.data_plane_convergence_us / 1000))

    flow_names_table_rows = ["Server IPv4_1 - Server IPv4_2",
                             "Server IPv6_2 - Server IPv6_1",
                             "Server IPv4_1 - T1", "Server IPv6_2 - T1",
                             "T1 - Server IPv4_1", "T1 - Server IPv6_2"]
    for j, i in enumerate(flow_names_table_rows):
        table.append([reboot_type, i, dp[j], float(0.0)])
    table.append([reboot_type, 'BGP Control Plane Up Time', float(0.0),
                  float(bgp_up_time) * 1000])
    table.append([reboot_type, 'Loopback Up Time', float(0.0),
                  float(loopback_up_time) * 1000])
    columns = ['Reboot Type', 'Traffic Item Name',
               'Data Plane Convergence Time (ms)', 'Time (ms)']
    logger.info("\n%s" % tabulate(table, headers=columns, tablefmt="psql"))
