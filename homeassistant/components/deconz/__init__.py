"""
Support for deCONZ devices.

For more details about this component, please refer to the documentation at
https://home-assistant.io/components/deconz/
"""

import asyncio
import logging
import voluptuous as vol

from homeassistant.const import (
    CONF_API_KEY, CONF_HOST, CONF_PORT, EVENT_HOMEASSISTANT_STOP)
from homeassistant.components.discovery import SERVICE_DECONZ
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import discovery
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.json import load_json, save_json

REQUIREMENTS = ['pydeconz==24']

_LOGGER = logging.getLogger(__name__)

DOMAIN = 'deconz'

CONFIG_FILE = 'deconz.conf'

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Optional(CONF_HOST): cv.string,
        vol.Optional(CONF_API_KEY): cv.string,
        vol.Optional(CONF_PORT, default=80): cv.port,
    })
}, extra=vol.ALLOW_EXTRA)

SERVICE_FIELD = 'field'
SERVICE_DATA = 'data'

SERVICE_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD): cv.string,
    vol.Required(SERVICE_DATA): cv.string,
})

CONFIG_INSTRUCTIONS = """
Unlock your deCONZ gateway to register with Home Assistant.

1. [Go to deCONZ system settings](http://{}:{}/edit_system.html)
2. Press "Unlock Gateway" button

[deCONZ platform documentation](https://home-assistant.io/components/deconz/)
"""


@asyncio.coroutine
def async_setup(hass, config):
    """Setup services and configuration for deCONZ component."""
    result = False
    config_file = yield from hass.async_add_job(
        load_json, hass.config.path(CONFIG_FILE))

    @asyncio.coroutine
    def async_deconz_discovered(service, discovery_info):
        """Called when deCONZ gateway has been found."""
        deconz_config = {}
        deconz_config[CONF_HOST] = discovery_info.get(CONF_HOST)
        deconz_config[CONF_PORT] = discovery_info.get(CONF_PORT)
        yield from async_request_configuration(hass, config, deconz_config)

    if config_file:
        result = yield from async_setup_deconz(hass, config, config_file)

    if not result and DOMAIN in config and CONF_HOST in config[DOMAIN]:
        deconz_config = config[DOMAIN]
        if CONF_API_KEY in deconz_config:
            result = yield from async_setup_deconz(hass, config, deconz_config)
        else:
            yield from async_request_configuration(hass, config, deconz_config)
            return True

    if not result:
        discovery.async_listen(hass, SERVICE_DECONZ, async_deconz_discovered)

    return True


@asyncio.coroutine
def async_setup_deconz(hass, config, deconz_config):
    """Setup deCONZ session.

    Load config, group, light and sensor data for server information.
    Start websocket for push notification of state changes from deCONZ.
    """
    from pydeconz import DeconzSession
    websession = async_get_clientsession(hass)
    deconz = DeconzSession(hass.loop, websession, **deconz_config)
    result = yield from deconz.async_load_parameters()
    if result is False:
        _LOGGER.error("Failed to communicate with deCONZ.")
        return False

    hass.data[DOMAIN] = deconz

    for component in ['binary_sensor', 'light', 'scene', 'sensor']:
        hass.async_add_job(discovery.async_load_platform(
            hass, component, DOMAIN, {}, config))
    deconz.start()

    @asyncio.coroutine
    def async_configure(call):
        """Set attribute of device in deCONZ.

        Field is a string representing a specific device in deCONZ
        e.g. field='/lights/1/state'.
        Data is a json object with what data you want to alter
        e.g. data={'on': true}.
        {
            "field": "/lights/1/state",
            "data": {"on": true}
        }
        See Dresden Elektroniks REST API documentation for details:
        http://dresden-elektronik.github.io/deconz-rest-doc/rest/
        """
        deconz = hass.data[DOMAIN]
        field = call.data.get(SERVICE_FIELD)
        data = call.data.get(SERVICE_DATA)
        yield from deconz.async_put_state(field, data)
    hass.services.async_register(
        DOMAIN, 'configure', async_configure,
        schema=SERVICE_SCHEMA)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, deconz.close)
    return True


@asyncio.coroutine
def async_request_configuration(hass, config, deconz_config):
    """Request configuration steps from the user."""
    configurator = hass.components.configurator

    @asyncio.coroutine
    def async_configuration_callback(data):
        """Set up actions to do when our configuration callback is called."""
        from pydeconz.utils import async_get_api_key
        api_key = yield from async_get_api_key(hass.loop, **deconz_config)
        if api_key:
            deconz_config[CONF_API_KEY] = api_key
            result = yield from async_setup_deconz(hass, config, deconz_config)
            if result:
                yield from hass.async_add_job(save_json,
                                              hass.config.path(CONFIG_FILE),
                                              deconz_config)
                configurator.async_request_done(request_id)
                return
            else:
                configurator.async_notify_errors(
                    request_id, "Couldn't load configuration.")
        else:
            configurator.async_notify_errors(
                request_id, "Couldn't get an API key.")
        return

    instructions = CONFIG_INSTRUCTIONS.format(
        deconz_config[CONF_HOST], deconz_config[CONF_PORT])

    request_id = configurator.async_request_config(
        "deCONZ", async_configuration_callback,
        description=instructions,
        entity_picture="/static/images/logo_deconz.jpeg",
        submit_caption="I have unlocked the gateway",
    )
