"""Support for Denon Network Receivers."""

from __future__ import annotations

import logging

import telnetlib  # pylint: disable=deprecated-module
import voluptuous as vol
import time

from homeassistant.components.media_player import (
    PLATFORM_SCHEMA as MEDIA_PLAYER_PLATFORM_SCHEMA,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "Music station"

SUPPORT_DENON = (
    MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.SELECT_SOURCE
)
SUPPORT_MEDIA_MODES = (
    MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PLAY
)

PLATFORM_SCHEMA = MEDIA_PLAYER_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    }
)

NORMAL_INPUTS = {
    "CD": "CD",
    "DVD": "DVD",
    "Blu-Ray": "BD",
    "TV": "TV",
    "Satellite / Cable": "SAT/CBL",
    "Game": "GAME",
    "Game 2": "GAME2",
    "Front AUX": "V.AUX",
    "Dock": "DOCK",
}

MEDIA_MODES = {
    "Tuner": "TUNER",
    "Media server": "SERVER",
    "iPod dock": "IPOD",
    "Net/USB": "NET/USB",
    "Rapsody": "RHAPSODY",
    "Napster": "NAPSTER",
    "Pandora": "PANDORA",
    "LastFM": "LASTFM",
    "Flickr": "FLICKR",
    "Favorites": "FAVORITES",
    "Internet Radio": "IRADIO",
    "USB/iPod": "USB/IPOD",
    "USB": "USB",
}

# Sub-modes of 'NET/USB'
# {'USB': 'USB', 'iPod Direct': 'IPD', 'Internet Radio': 'IRP',
#  'Favorites': 'FVP'}


def setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Denon platform."""
    denon = DenonDevice(config[CONF_NAME], config[CONF_HOST])
    add_entities([denon])

class TelnetError(Exception):
    pass

class DenonDevice(MediaPlayerEntity):
    """Representation of a Denon device."""

    def __init__(self, name, host):
        """Initialize the Denon device."""
        self._name : str = name
        self._host : str = host
        self._pwstate : str = "PWSTANDBY"
        self._volume : int = 0
        # Initial value 60dB, changed if we get a MVMAX
        self._volume_max : int = 60
        self._source_list : dict = dict()
        self._muted : bool = False
        self._mediasource : str = ""
        self._mediainfo : str = ""

        self._should_setup_sources = True

    def _connect_telnet(self) -> telnetlib.Telnet:
        try:
            _LOGGER.debug("Attempting connection to %s", self._host)
            return telnetlib.Telnet(self._host)
        except OSError as e:
            _LOGGER.error("Connection to %s failed: %s", host, str(e))
            raise TelnetError("could not open connection: "+str(e))

    @classmethod
    def _read_telnet(self,telnet) -> str:
        try:
            r = telnet.read_very_eager().decode("ASCII")
            _LOGGER.debug("Partial Read: %s", r)
            return r
        except EOFError as e:
            _LOGGER.error("read failed: %s", str(e))
            raise TelnetError("connection closed unexpectedly: "+str(e))

    @classmethod
    def _write_telnet(self,telnet,command):
        _LOGGER.debug("Sending: %s", command)
        try:
            telnet.write(command.encode("ASCII") + b"\r")
        except EOFError as e:
            _LOGGER.error("write failed: %s", str(e))
            raise TelnetError("connection closed unexpectedly: "+str(e))

    @classmethod
    def _read_telnet_until_pause(self, telnet) -> str:
        rcv = ""
        starttime = time.monotonic_ns()
        time_since_data = starttime + (1000 * 1000 * 1000) #give extra 1000ms initially for high ping
        while True:
            incoming = self._read_telnet(telnet)
            rcv += incoming
            time.sleep(0.01)
            t_now = time.monotonic_ns()
            if len(incoming) > 1:
                time_since_data = t_now
            if t_now - time_since_data > (200 * 1000 * 1000): #wait 200ms for stop of data flow
                break
            if t_now - starttime > (2000 * 1000 * 1000): #wait for nomore than 1000ms
                break
        _LOGGER.debug("Full Read in %.1fms: %s", ((time.monotonic_ns() - starttime) / 1000) / 1000, rcv)
        return rcv

    @classmethod
    def telnet_request(self, telnet, command, all_lines=False):
        """Execute `command` and return the response."""
        self._read_telnet(telnet) #clear buffer
        self._write_telnet(telnet, command)
        lines = self._read_telnet_until_pause(telnet).split("\r")
        lines = [l.strip() for l in lines]
        _LOGGER.debug("Received: %s", str(lines))
        if all_lines:
            return lines
        return lines[0] if lines else ""

    def telnet_command(self, command) -> None:
        """Establish a telnet connection and sends `command`."""
        telnet = self._connect_telnet()
        self._write_telnet(telnet,command)
        telnet.close()

    @classmethod
    def _get_data(self, raw:str,key:str):
        """Gets data after key"""
        start = raw.index(key) + len(key)
        end = raw.find("\r", start)
        return raw[start:end]

    def _setup_sources(self, telnet):
        # NSFRN - Network name
        if self._name is None:
            nsfrn = self.telnet_request(telnet, "NSFRN ?")
            for line in nsfrn.split("\r"):
                try:
                    self._name = self._get_data(line,"NSFRN ")
                except ValueError:
                    pass

        # SSFUN - Configured sources with (optional) names
        self._source_list = dict()
        for line in self.telnet_request(telnet, "SSFUN ?", all_lines=True):
            try:
                ssfun = self._get_data(line,"SSFUN")
                ssfun = ssfun.split(" ", 1)
            except ValueError:
                continue

            source = ssfun[0]
            if len(ssfun) == 2 and ssfun[1]:
                configured_name = ssfun[1]
            else:
                # No name configured, reusing the source name
                configured_name = source

            self._source_list[configured_name] = source
        if len(self._source_list) == 0: #if SSFUN unsupported
            self._source_list = NORMAL_INPUTS | MEDIA_MODES

        # SSSOD - Deleted sources
        for line in self.telnet_request(telnet, "SSSOD ?", all_lines=True):
            try:
                data = self._get_data(line,"SSSOD")
            except ValueError:
                continue
            source, status = data.split(" ", 1)
            if status == "DEL":
                for pretty_name, name in self._source_list.items():
                    if source == name:
                        del self._source_list[pretty_name]
                        break

    def update(self) -> None:
        """Get the latest details from the device."""
        self.do_update()

    def do_update(self) -> bool:
        """Get the latest details from the device, as boolean."""
        try:
            telnet = self._connect_telnet()
        except TelnetError:
            return False

        if self._should_setup_sources:
            self._setup_sources(telnet)
            self._should_setup_sources = False

        self._pwstate = self.telnet_request(telnet, "PW?")
        for line in self.telnet_request(telnet, "MV?", all_lines=True):
            if line.startswith("MVMAX "):
                # only grab two digit max, don't care about any half digit
                self._volume_max = int(line[len("MVMAX ") : len("MVMAX XX")])
                continue
            if line.startswith("MV"):
                self._volume = int(line.removeprefix("MV"))
        self._muted = self.telnet_request(telnet, "MU?") == "MUON"
        self._mediasource = self._get_data(
            self.telnet_request(telnet, "SI?"),
            "SI"
        )

        if self._mediasource in MEDIA_MODES.values():
            self._mediainfo = ""
            answer_codes = [
                "NSE0",
                "NSE1X",
                "NSE2X",
                "NSE3X",
                "NSE4",
                "NSE5",
                "NSE6",
                "NSE7",
                "NSE8",
            ]
            answer = self.telnet_request(telnet, "NSE", all_lines=True)
            self._mediainfo += "\n".join(
                [self._get_data(answer, code) for code in answer_codes] 
            )
        else:
            self._mediainfo = self.source

        telnet.close()
        return True

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def state(self) -> MediaPlayerState | None:
        """Return the state of the device."""
        if self._pwstate == "PWSTANDBY":
            return MediaPlayerState.OFF
        if self._pwstate == "PWON":
            return MediaPlayerState.ON

        return None

    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        return self._volume / self._volume_max

    @property
    def is_volume_muted(self):
        """Return boolean if volume is currently muted."""
        return self._muted

    @property
    def source_list(self):
        """Return the list of available input sources."""
        return sorted(self._source_list.keys())

    @property
    def media_title(self):
        """Return the current media info."""
        return self._mediainfo

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Flag media player features that are supported."""
        if self._mediasource in MEDIA_MODES.values():
            return SUPPORT_DENON | SUPPORT_MEDIA_MODES
        return SUPPORT_DENON

    @property
    def source(self) -> str | None:
        """Return the current input source."""
        for pretty_name, name in self._source_list.items():
            if self._mediasource == name:
                return pretty_name
        return None

    def turn_off(self) -> None:
        """Turn off media player."""
        self.telnet_command("PWSTANDBY")

    def volume_up(self) -> None:
        """Volume up media player."""
        self.telnet_command("MVUP")

    def volume_down(self) -> None:
        """Volume down media player."""
        self.telnet_command("MVDOWN")

    def set_volume_level(self, volume: float) -> None:
        """Set volume level, range 0..1."""
        self.telnet_command(f"MV{round(volume * self._volume_max):02}")

    def mute_volume(self, mute: bool) -> None:
        """Mute (true) or unmute (false) media player."""
        mute_status = "ON" if mute else "OFF"
        self.telnet_command(f"MU{mute_status}")

    def media_play(self) -> None:
        """Play media player."""
        self.telnet_command("NS9A")

    def media_pause(self) -> None:
        """Pause media player."""
        self.telnet_command("NS9B")

    def media_stop(self) -> None:
        """Pause media player."""
        self.telnet_command("NS9C")

    def media_next_track(self) -> None:
        """Send the next track command."""
        self.telnet_command("NS9D")

    def media_previous_track(self) -> None:
        """Send the previous track command."""
        self.telnet_command("NS9E")

    def turn_on(self) -> None:
        """Turn the media player on."""
        self.telnet_command("PWON")
        self._should_setup_sources = True

    def select_source(self, source: str) -> None:
        """Select input source."""
        src_denon = self._source_list.get(source,source)
        self.telnet_command(f"SI{src_denon}")
