import ssl
import socket
import ipaddress
from datetime import timedelta
import logging
from typing import List

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_HOST, CONF_USERNAME, CONF_PASSWORD, CONF_DIRECTORY

SCAN_INTERVAL = timedelta(minutes=5)
_LOGGER = logging.getLogger(__name__)

def should_pass_hostname(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        return True

def wrap_connection(sock, host):
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    if should_pass_hostname(host):
        return context.wrap_socket(sock, server_hostname=host)
    else:
        return context.wrap_socket(sock, server_hostname=None)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    directory = entry.data[CONF_DIRECTORY]

    select = FTPFileDropdown(hass, host, username, password, directory)
    await select.async_update_options()
    async_add_entities([select])

def simple_ftp_nlst(host: str, username: str, password: str, folder: str) -> List[str]:
    if not host:
        raise ValueError("Host is empty, cannot connect to FTP server.")

    sock = socket.create_connection((host, 990), timeout=10)
    ssl_sock = wrap_connection(sock, host)

    def send(cmd):
        ssl_sock.sendall((cmd + '\r\n').encode())

    def recv():
        data = ssl_sock.recv(4096)
        return data.decode(errors='ignore')

    recv()
    send(f"USER {username}")
    recv()
    send(f"PASS {password}")
    recv()

    if folder:
        send(f"CWD {folder}")
        recv()

    send('PASV')
    pasv_response = recv()

    start = pasv_response.find('(') + 1
    end = pasv_response.find(')', start)
    parts = pasv_response[start:end].split(',')
    ip = '.'.join(parts[:4])
    port = (int(parts[4]) << 8) + int(parts[5])

    data_sock = socket.create_connection((ip, port), timeout=10)
    data_ssl_sock = wrap_connection(data_sock, host)

    send('NLST')
    recv()

    data = b''
    while True:
        chunk = data_ssl_sock.recv(4096)
        if not chunk:
            break
        data += chunk

    data_ssl_sock.close()
    ssl_sock.close()

    files = data.decode(errors='ignore').splitlines()
    return files

class FTPFileDropdown(SelectEntity):
    def __init__(self, hass: HomeAssistant, host: str, username: str, password: str, directory: str):
        self.hass = hass
        self._host = host
        self._username = username
        self._password = password
        self._directory = directory
        self._attr_name = "FTP File Selector"
        self._attr_options = []
        self._attr_current_option = None

    async def async_added_to_hass(self):
        await self.async_update()

    async def async_update_options(self):
        files = await self.hass.async_add_executor_job(self.get_files)
        self._attr_options = files
        if files and (self._attr_current_option not in files):
            self._attr_current_option = files[0]

    def get_files(self) -> List[str]:
        try:
            _LOGGER.warning(f"Preparing to connect with HOST='{self._host}' USER='{self._username}' DIR='{self._directory}'")
            files = simple_ftp_nlst(self._host, self._username, self._password, self._directory)
            _LOGGER.info(f"Found {len(files)} file(s): {files}")
            return files
        except Exception as e:
            _LOGGER.error(f"FTP connection/listing failed: {e}")
            return []

    async def async_update(self) -> None:
        await self.async_update_options()

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self.async_write_ha_state()
