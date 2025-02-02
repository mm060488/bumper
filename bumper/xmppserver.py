"""XMPP module."""
import asyncio
import base64
import re
import ssl
import uuid
import xml.etree.ElementTree as ET
from asyncio import transports
from typing import Optional

import bumper
from bumper.db import (
    bot_add,
    bot_get,
    bot_set_xmpp,
    check_authcode,
    client_add,
    client_get,
    client_set_xmpp,
)

xmppserverlog = bumper.get_logger("xmppserver")
boterrorlog = bumper.get_logger("boterror")


class XMPPServer:
    """XMPP server."""

    server_id = "ecouser.net"
    clients: list["XMPPAsyncClient"] = []
    exit_flag = False
    server = None

    def __init__(self, host: str, port: int):
        # Initialize bot server
        self._host = host
        self._port = port
        self.xmpp_protocol = lambda: XMPPServer_Protocol()

    async def start_async_server(self) -> None:
        """Start server."""
        try:
            xmppserverlog.info(f"Starting XMPP Server at {self._host}:{self._port}")

            loop = asyncio.get_running_loop()

            self.server = await loop.create_server(
                self.xmpp_protocol, host=self._host, port=self._port
            )

            self.server_coro = loop.create_task(self.server.serve_forever())

        except Exception as e:
            xmppserverlog.exception(f"{e}")
            raise e

    def disconnect(self) -> None:
        """Disconnect."""
        xmppserverlog.debug("waiting for all clients to disconnect")
        for client in self.clients:
            client._disconnect()

        self.exit_flag = True
        xmppserverlog.debug("shutting down")
        self.server_coro.cancel()


class XMPPServer_Protocol(asyncio.Protocol):
    """XMPP server protocol."""

    client_id = None
    exit_flag = False
    _client: Optional["XMPPAsyncClient"] = None

    def connection_made(self, transport: transports.BaseTransport) -> None:
        """Establish connection."""
        if self._client:  # Existing client... upgrading to TLS
            xmppserverlog.debug(f"Upgraded connection for {self._client.address}")
            self._client.transport = transport
        else:
            client = XMPPAsyncClient(transport)
            self._client = client
            XMPPServer.clients.append(client)
            self._client.state = getattr(client, "CONNECT")
            xmppserverlog.debug(f"New Connection from {client.address}")

    def connection_lost(self, exc: Exception | None) -> None:
        """Lost connection."""
        if self._client:
            XMPPServer.clients.remove(self._client)
            self._client.set_state("DISCONNECT")
            xmppserverlog.debug(
                "End Connection for ({}:{} | {})".format(
                    self._client.address[0],
                    self._client.address[1],
                    self._client.bumper_jid,
                )
            )

    def data_received(self, data: bytes) -> None:
        """Parse received data."""
        if self._client:
            self._client.parse_data(data)


class XMPPAsyncClient:
    """XMPP client."""

    IDLE = 0
    CONNECT = 1
    INIT = 2
    BIND = 3
    READY = 4
    DISCONNECT = 5
    UNKNOWN = 0
    BOT = 1
    CONTROLLER = 2
    TLSUpgraded = False

    def __init__(self, transport: transports.BaseTransport):
        self.type = self.UNKNOWN
        self.state = self.IDLE
        self.address = transport.get_extra_info("peername")
        self.transport = transport
        self.clientresource = ""
        self.devclass = ""
        self.bumper_jid = ""
        self.uid = ""
        self.log_sent_message = True  # Set to true to log sends
        self.log_incoming_data = True  # Set to true to log sends
        xmppserverlog.debug(f"new client with ip {self.address}")

    def send(self, command: str) -> None:
        """Send command."""
        try:
            if self.log_sent_message:
                xmppserverlog.debug(
                    "send to ({}:{} | {}) - {}".format(
                        self.address[0], self.address[1], self.bumper_jid, command
                    )
                )
            if isinstance(self.transport, transports.WriteTransport):
                self.transport.write(command.encode())

        except Exception as e:
            xmppserverlog.exception(f"{e}")

    def _disconnect(self) -> None:
        try:

            bot = bot_get(self.uid)
            if bot:
                bot_set_xmpp(bot["did"], False)

            client = client_get(self.clientresource)
            if client:
                client_set_xmpp(client["resource"], False)

            self.transport.close()

        except Exception as e:
            xmppserverlog.error(f"{e}")

    def _tag_strip_uri(self, tag: str) -> str:
        try:
            if tag[0] == "{":
                _, _, tag = tag[1:].partition("}")
        except Exception as e:
            xmppserverlog.error(f"{e}")
        return tag

    def set_state(self, state: str) -> None:
        """Set state."""
        try:
            new_state = getattr(XMPPAsyncClient, state)
            if self.state > new_state:
                raise Exception(
                    "{} illegal state change {}->{}".format(
                        self.address, self.state, new_state
                    )
                )

            xmppserverlog.debug(
                "({}:{} | {}) state: {}".format(
                    self.address[0], self.address[1], self.bumper_jid, state
                )
            )

            self.state = new_state

            if new_state == 5:
                self._disconnect()

        except Exception as e:
            xmppserverlog.error(f"{e}")

    def _handle_ctl(self, xml: ET.Element, data: str) -> None:
        try:

            if "roster" in data:
                # Return not-implemented for roster
                self.send(
                    '<iq type="error" id="{}"><error type="cancel" code="501"><feature-not-implemented xmlns="urn:ietf:params:xml:ns:xmpp-stanzas"/></error></iq>'.format(
                        xml.get("id")
                    )
                )
                return

            if "disco#items" in data:
                # Return  not-implemented for disco#items
                self.send(
                    '<iq type="error" id="{}"><error type="cancel" code="501"><feature-not-implemented xmlns="urn:ietf:params:xml:ns:xmpp-stanzas"/></error></iq>'.format(
                        xml.get("id")
                    )
                )
                return

            if "disco#info" in data:
                # Return not-implemented for disco#info
                self.send(
                    '<iq type="error" id="{}"><error type="cancel" code="501"><feature-not-implemented xmlns="urn:ietf:params:xml:ns:xmpp-stanzas"/></error></iq>'.format(
                        xml.get("id")
                    )
                )
                return

            if xml.get("type") == "set":
                if (
                    "com:sf" in data and xml.get("to") == "rl.ecorobot.net"
                ):  # Android bind? Not sure what this does yet.
                    self.send(
                        '<iq id="{}" to="{}@{}/{}" from="rl.ecorobot.net" type="result"/>'.format(
                            xml.get("id"),
                            self.uid,
                            XMPPServer.server_id,
                            self.clientresource,
                        )
                    )

            if len(xml[0]) > 0:
                ctl = xml[0][0]
                if ctl.get("admin") and self.type == self.BOT:
                    xmppserverlog.debug(
                        "admin username received from bot: {}".format(ctl.get("admin"))
                    )
                    # XMPPServer.client_id = ctl.get("admin")
                    return

            # forward
            for client in XMPPServer.clients:
                if (
                    client.bumper_jid != self.bumper_jid
                    and client.state == client.READY
                ):
                    ctl_to = xml.get("to")
                    if "from" not in xml.attrib:
                        xml.attrib["from"] = f"{self.bumper_jid}"
                    rxmlstring = ET.tostring(xml).decode("utf-8")
                    # clean up string to remove namespaces added by ET
                    rxmlstring = rxmlstring.replace("xmlns:ns0=", "xmlns=")
                    rxmlstring = rxmlstring.replace("ns0:", "")
                    rxmlstring = rxmlstring.replace('iq xmlns="com:ctl"', "iq")
                    rxmlstring = rxmlstring.replace("<query", '<query xmlns="com:ctl"')

                    if client.type == self.BOT and ctl_to:
                        if client.uid.lower() in ctl_to.lower():
                            xmppserverlog.debug(f"Sending ctl to bot: {rxmlstring}")
                            client.send(rxmlstring)

        except Exception as e:
            xmppserverlog.error(f"{e}")

    def _handle_ping(self, xml: ET.Element) -> None:
        try:
            pingto = xml.get("to")
            if pingto and pingto.find("@") == -1:  # No to address
                # Ping to server - respond
                pingresp = '<iq type="result" id="{}" from="{}" />'.format(
                    xml.get("id"), xml.get("to")
                )
                # xmppserverlog.debug("Server Ping resp: {}".format(pingresp))
                self.send(pingresp)

            else:
                pingfrom = self.bumper_jid
                if "from" not in xml.attrib:
                    xml.attrib["from"] = f"{pingfrom}"
                pingstring = ET.tostring(xml).decode("utf-8")
                # clean up string to remove namespaces added by ET
                pingstring = pingstring.replace("xmlns:ns0=", "xmlns=")
                pingstring = pingstring.replace("ns0:", "")
                pingstring = pingstring.replace('iq xmlns="urn:xmpp:ping"', "iq")
                pingstring = pingstring.replace("<ping", '<ping xmlns="urn:xmpp:ping"')

                for client in XMPPServer.clients:
                    if (
                        client.bumper_jid != self.bumper_jid
                        and client.state == client.READY
                        and pingto
                    ):
                        if client.uid.lower() in pingto.lower():
                            client.send(pingstring)

        except Exception as e:
            xmppserverlog.exception(f"{e}")

    async def schedule_ping(self, time: int) -> None:
        """Schedule ping."""
        if not self.state == 5:  # disconnected
            pingstring = "<iq from='{}' to='{}' id='s2c1' type='get'><ping xmlns='urn:xmpp:ping'/></iq>".format(
                XMPPServer.server_id, self.bumper_jid
            )
            self.send(pingstring)
            await asyncio.sleep(time)
            asyncio.Task(self.schedule_ping(time))

    def _handle_result(self, xml: ET.Element, data: str) -> None:
        try:
            ctl_to = xml.get("to")
            if "from" not in xml.attrib:
                xml.attrib["from"] = f"{self.bumper_jid}"
            if "errno" in data:
                xmppserverlog.error(f"Error from bot - {data}")
            if (
                "errno='103'" in data
            ):  # No permissions, usually if bot was last on Ecovac network, Bumper will try to add fuid user as owner
                if self.type == self.BOT:
                    xmppserverlog.info(
                        "Bot reported user has no permissions, Bumper will attempt to add user to bot. This is typical if bot was last on Ecovacs Network."
                    )
                    xquery = list(xml)
                    ctl = list(xquery[0])
                    if "error" in ctl[0].attrib:
                        ctlerr = ctl[0].attrib["error"]
                        adminuser = ctlerr.replace(
                            "permission denied, please contact ", ""
                        )
                        adminuser = adminuser.replace(" ", "")
                    elif "admin" in ctl[0].attrib:
                        adminuser = ctl[0].attrib["admin"]
                    if ctl_to and not (
                        adminuser.startswith("fuid_")
                        or adminuser.startswith("fusername_")
                        or bumper.use_auth
                    ):
                        # if not fuid_ then its ecovacs OR ignore bumper auth
                        # TODO: Implement auth later, should this user have access to bot?

                        # Add user jid to bot
                        newuser = ctl_to.split("/")[0]
                        adduser = '<iq type="set" id="{}" from="{}" to="{}"><query xmlns="com:ctl"><ctl td="AddUser" id="0000" jid="{}" /></query></iq>'.format(
                            uuid.uuid4(), adminuser, self.bumper_jid, newuser
                        )
                        xmppserverlog.debug(f"Adding User to bot - {adduser}")
                        self.send(adduser)

                        # Add user ACs - Manage users, settings, and clean (full access)
                        adduseracs = '<iq type="set" id="{}" from="{}" to="{}"><query xmlns="com:ctl"><ctl td="SetAC" id="1111" jid="{}"><acs><ac name="userman" allow="1"/><ac name="setting" allow="1"/><ac name="clean" allow="1"/></acs></ctl></query></iq>'.format(
                            uuid.uuid4(), adminuser, self.bumper_jid, newuser
                        )
                        xmppserverlog.debug(f"Add User ACs to bot - {adduseracs}")
                        self.send(adduseracs)

                        # GetUserInfo - Just to confirm it set correctly
                        self.send(
                            '<iq type="set" id="{}" from="{}" to="{}"><query xmlns="com:ctl"><ctl td="GetUserInfo" id="4444" /><UserInfos/></query></iq>'.format(
                                uuid.uuid4(), adminuser, self.bumper_jid
                            )
                        )

            else:
                rxmlstring = ET.tostring(xml).decode("utf-8")
                # clean up string to remove namespaces added by ET
                rxmlstring = rxmlstring.replace("xmlns:ns0=", "xmlns=")
                rxmlstring = rxmlstring.replace("ns0:", "")
                rxmlstring = rxmlstring.replace('iq xmlns="com:ctl"', "iq")
                rxmlstring = rxmlstring.replace("<query", '<query xmlns="com:ctl"')
                if self.type == self.BOT:
                    if ctl_to == "de.ecorobot.net":  # Send to all clients
                        xmppserverlog.debug(
                            "Sending to all clients because of de: {}".format(
                                rxmlstring
                            )
                        )
                        for client in XMPPServer.clients:
                            client.send(rxmlstring)

                to = xml.get("to")
                if to and to.find("@") == -1:  # No to address
                    ctl_to = to
                else:
                    assert ctl_to
                    ctl_to = "{}@ecouser.net".format(ctl_to.split("@")[0])

                for client in XMPPServer.clients:
                    if (
                        client.bumper_jid != self.bumper_jid
                        and client.state == client.READY
                    ):
                        if "@" not in ctl_to:  # No user@, send to all clients?
                            # TODO: Revisit later, this may be wrong
                            client.send(rxmlstring)

                        elif (
                            client.uid.lower() in ctl_to.lower()
                        ):  # If client matches TO=
                            xmppserverlog.debug(
                                "Sending from {} to client {}: {}".format(
                                    self.uid, client.uid, rxmlstring
                                )
                            )
                            client.send(rxmlstring)

        except Exception as e:
            xmppserverlog.exception(f"{e}")

    def _handle_connect(self, data: bytes, xml: None | ET.Element = None) -> None:
        try:

            if self.state == self.CONNECT:
                if xml is None:
                    # Client first connecting, send our features
                    if data.decode("utf-8").find("jabber:client") > -1:
                        sc = data.decode("utf-8").find("to=")
                        ec = data.decode("utf-8").find(".ecorobot.net")
                        if ec > -1:
                            self.devclass = data.decode("utf-8")[sc + 4 : ec]
                        # ack jabbr:client
                        # Send stream tag to client, acknowledging connection
                        self.send(
                            '<stream:stream xmlns:stream="http://etherx.jabber.org/streams" xmlns="jabber:client" version="1.0" id="1" from="{}">'.format(
                                XMPPServer.server_id
                            )
                        )

                        # Send STARTTLS to client with auth mechanisms
                        if not self.TLSUpgraded:
                            # With STARTTLS #https://xmpp.org/rfcs/rfc3920.html
                            self.send(
                                '<stream:features><starttls xmlns="urn:ietf:params:xml:ns:xmpp-tls"><required/></starttls><mechanisms xmlns="urn:ietf:params:xml:ns:xmpp-sasl"><mechanism>PLAIN</mechanism></mechanisms></stream:features>'
                            )

                        else:
                            # Already using TLS send authentication support for SASL
                            self.send(
                                '<stream:features><mechanisms xmlns="urn:ietf:params:xml:ns:xmpp-sasl"><mechanism>PLAIN</mechanism></mechanisms></stream:features>'
                            )

                    else:
                        self.send("</stream>")

                else:
                    if (
                        "urn:ietf:params:xml:ns:xmpp-sasl" in xml.tag
                    ):  # Handle SASL Auth
                        self._handle_sasl_auth(xml)
                    else:
                        xmppserverlog.error(f"Couldn't handle: {xml}")

            elif self.state == self.INIT:
                if xml is None:
                    # Client getting session after authentication
                    if data.decode("utf-8").find("jabber:client") > -1:
                        # ack jabbr:client
                        self.send(
                            '<stream:stream xmlns:stream="http://etherx.jabber.org/streams" xmlns="jabber:client" version="1.0" id="1" from="{}">'.format(
                                XMPPServer.server_id
                            )
                        )

                        self.send(
                            '<stream:features><bind xmlns="urn:ietf:params:xml:ns:xmpp-bind"/><session xmlns="urn:ietf:params:xml:ns:xmpp-session"/></stream:features>'
                        )

                else:  # Handle init bind
                    if len(xml):
                        child = self._tag_strip_uri(xml[0].tag)
                    else:
                        child = None

                    if xml.tag == "iq":
                        if child == "bind":
                            self._handle_bind(xml)
                    else:
                        xmppserverlog.error(f"Couldn't handle: {xml}")

        except Exception as e:
            xmppserverlog.exception(f"{e}")

    async def _handle_starttls(self, _: bytes) -> None:
        try:
            if not self.TLSUpgraded:
                self.TLSUpgraded = True  # Set TLSUpgraded true to prevent further attempts to upgrade connection
                xmppserverlog.debug(
                    "Upgrading connection with STARTTLS for {}:{}".format(
                        self.address[0], self.address[1]
                    )
                )
                self.send(
                    "<proceed xmlns='urn:ietf:params:xml:ns:xmpp-tls'/>"
                )  # send process to client

                # After proceed the connection should be upgraded to TLS
                loop = asyncio.get_event_loop()
                transport = self.transport
                protocol = self.transport.get_protocol()

                ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
                ssl_ctx.load_cert_chain(bumper.server_cert, bumper.server_key)
                ssl_ctx.load_verify_locations(cafile=bumper.ca_cert)

                new_transport = await loop.start_tls(
                    transport, protocol, ssl_ctx, server_side=True
                )
                protocol.connection_made(new_transport)

        except Exception as e:
            xmppserverlog.exception(f"{e}")

    def _handle_sasl_auth(self, xml: ET.Element) -> None:
        try:
            assert xml.text
            saslauth = base64.b64decode(xml.text).decode("utf-8").split("/")
            username = saslauth[0]
            username = saslauth[0].split("\x00")[1]
            authcode = ""
            self.uid = username
            if len(saslauth) > 1:
                resource = saslauth[1]
                self.clientresource = resource
            elif len(saslauth[0].split("\x00")) > 2:
                resource = saslauth[0].split("\x00")[2]
                self.clientresource = resource

            if len(saslauth) > 2:
                authcode = saslauth[2]

            if self.devclass:  # if there is a devclass it is a bot
                bot_add(self.uid, self.uid, self.devclass, "atom", "eco-legacy")
                self.type = self.BOT
                xmppserverlog.info(f"bot authenticated SN: {self.uid}")
                # Send response
                self.send(
                    '<success xmlns="urn:ietf:params:xml:ns:xmpp-sasl"/>'
                )  # Success

                # Client authenticated, move to next state
                self.set_state("INIT")

            else:
                auth = False
                if check_authcode(self.uid, authcode):
                    auth = True
                elif not bumper.use_auth:
                    auth = True

                if auth:
                    self.type = self.CONTROLLER
                    client_add(self.uid, "bumper", self.clientresource)
                    xmppserverlog.info(f"client authenticated {self.uid}")

                    # Client authenticated, move to next state
                    self.set_state("INIT")

                    # Send response
                    self.send(
                        '<success xmlns="urn:ietf:params:xml:ns:xmpp-sasl"/>'
                    )  # Success

                else:
                    # Failed to authenticate
                    self.send(
                        '<response xmlns="urn:ietf:params:xml:ns:xmpp-sasl"/>'
                    )  # Fail

        except Exception as e:
            xmppserverlog.exception(f"{e}")

    def _handle_bind(self, xml: ET.Element) -> None:
        try:

            bot = bot_get(self.uid)
            if bot:
                bot_set_xmpp(bot["did"], True)

            client = client_get(self.clientresource)
            if client:
                client_set_xmpp(client["resource"], True)

            clientbindxml = list(xml)
            clientresourcexml = list(clientbindxml[0])
            if self.devclass:  # its a bot
                self.name = f"XMPP_Client_{self.uid}_{self.devclass}"
                self.bumper_jid = "{}@{}.ecorobot.net/atom".format(
                    self.uid, self.devclass
                )
                xmppserverlog.debug(
                    "new bot ({}:{} | {})".format(
                        self.address[0], self.address[1], self.bumper_jid
                    )
                )
                res = '<iq type="result" id="{}"><bind xmlns="urn:ietf:params:xml:ns:xmpp-bind"><jid>{}</jid></bind></iq>'.format(
                    xml.get("id"), self.bumper_jid
                )
            elif len(clientresourcexml) > 0:
                assert clientresourcexml[0].text
                self.clientresource = clientresourcexml[0].text
                self.name = f"XMPP_Client_{self.clientresource}"
                self.bumper_jid = "{}@{}/{}".format(
                    self.uid, XMPPServer.server_id, self.clientresource
                )
                xmppserverlog.debug(
                    "new client ({}:{} | {})".format(
                        self.address[0], self.address[1], self.bumper_jid
                    )
                )
                res = '<iq type="result" id="{}"><bind xmlns="urn:ietf:params:xml:ns:xmpp-bind"><jid>{}</jid></bind></iq>'.format(
                    xml.get("id"), self.bumper_jid
                )
            else:
                self.name = f"XMPP_Client_{self.uid}_{self.address}"
                self.bumper_jid = f"{self.uid}@{XMPPServer.server_id}"
                xmppserverlog.debug(
                    "new client ({}:{} | {})".format(
                        self.address[0], self.address[1], self.bumper_jid
                    )
                )
                res = '<iq type="result" id="{}"><bind xmlns="urn:ietf:params:xml:ns:xmpp-bind"><jid>{}</jid></bind></iq>'.format(
                    xml.get("id"), self.bumper_jid
                )

            self.set_state("BIND")
            self.send(res)

        except Exception as e:
            xmppserverlog.exception(f"{e}")

    def _handle_session(self, xml: ET.Element) -> None:
        res = '<iq type="result" id="{}" />'.format(xml.get("id"))
        self.set_state("READY")
        self.send(res)
        asyncio.Task(self.schedule_ping(30))

    def _handle_presence(self, xml: ET.Element) -> None:

        if len(xml) and xml[0].tag == "status":
            xmppserverlog.debug(
                "bot presence {} ".format(
                    ET.tostring(xml, encoding="utf-8").decode("utf-8")
                )
            )
            # Most likely a bot, possibly hello world in text

            # Send dummy return
            self.send(f'<presence to="{self.bumper_jid}"> dummy </presence>')

            # If it is a BOT, send extras
            if self.type == self.BOT:
                # get device info
                self.send(
                    '<iq type="set" id="14" to="{}" from="{}"><query xmlns="com:ctl"><ctl td="GetDeviceInfo"/></query></iq>'.format(
                        self.bumper_jid, XMPPServer.server_id
                    )
                )

        else:
            xmppserverlog.debug(
                "client presence - {} ".format(
                    ET.tostring(xml, encoding="utf-8").decode("utf-8")
                )
            )

            if xml.get("type") == "available":
                xmppserverlog.debug(
                    "client presence available - {} ".format(
                        ET.tostring(xml, encoding="utf-8").decode("utf-8")
                    )
                )

                # Send dummy return
                self.send(f'<presence to="{self.bumper_jid}"> dummy </presence>')
            elif xml.get("type") == "unavailable":
                xmppserverlog.debug(
                    "client presence unavailable (DISCONNECT) - {} ".format(
                        ET.tostring(xml, encoding="utf-8").decode("utf-8")
                    )
                )

                self.set_state("DISCONNECT")
            else:
                # Sometimes the android app sends these
                xmppserverlog.debug(
                    "client presence (UNKNOWN) - {} ".format(
                        ET.tostring(xml, encoding="utf-8")
                    )
                )
                # Send dummy return
                self.send(f'<presence to="{self.bumper_jid}"> dummy </presence>')

    def parse_data(self, data: bytes) -> None:
        """Parse data."""

        if data.decode("utf-8").startswith(
            "<?xml"
        ):  # Strip <?xml and add artificial root
            newdata = (
                re.sub(r"(<\?xml[^>]+\?>)", r"<root>", data.decode("utf-8")) + "</root>"
            )

        else:
            newdata = "<root>{}</root>".format(
                data.decode("utf-8")
            )  # Add artificial root

        try:
            root = ET.fromstring(newdata)
            for item in root.iter():
                if item.tag != "root":
                    if item.tag == "iq":
                        if self.log_incoming_data:
                            xmppserverlog.debug(
                                "from ({}:{} | {}) - {}".format(
                                    self.address[0],
                                    self.address[1],
                                    self.bumper_jid,
                                    str(
                                        ET.tostring(item, encoding="utf-8").decode(
                                            "utf-8"
                                        )
                                    ).replace("ns0:", ""),
                                )
                            )
                            if (
                                'td="error"' in newdata
                                or "errs=" in newdata
                                or 'k="DeviceAlert' in newdata
                            ):
                                boterrorlog.error(
                                    "Received Error from ({}:{} | {}) - {}".format(
                                        self.address[0],
                                        self.address[1],
                                        self.bumper_jid,
                                        newdata,
                                    )
                                )
                        self._handle_iq(item, newdata)
                        item.clear()

                    elif "auth" in item.tag:
                        if "urn:ietf:params:xml:ns:xmpp-sasl" in item.tag:  # SASL Auth
                            self._handle_sasl_auth(item)
                            item.clear()

                    elif "-tls" in item.tag:
                        if not self.TLSUpgraded:
                            asyncio.Task(self._handle_starttls(newdata.encode("utf-8")))

                    elif "presence" in item.tag:
                        self._handle_presence(item)
                        item.clear()

                    else:
                        if self.log_incoming_data:
                            xmppserverlog.debug(
                                "Unparsed Item - {}".format(
                                    str(
                                        ET.tostring(item, encoding="utf-8").decode(
                                            "utf-8"
                                        )
                                    ).replace("ns0:", "")
                                )
                            )

        except ET.ParseError as e:
            if (
                "no element found" in e.msg
            ):  # Element not closed or not all bytes received
                # Happens with connect stream often
                if "<stream:stream " in newdata:
                    if self.state == self.CONNECT or self.state == self.INIT:
                        self._handle_connect(newdata.encode("utf-8"))
                else:
                    if not (newdata == "" or newdata == " "):
                        xmppserverlog.error(f"xml parse error - {newdata} - {e}")

            elif "not well-formed (invalid token)" in e.msg:
                # If a lone </stream:stream> - client is signalling end of session/disconnect
                if "</stream:stream>" not in newdata:
                    xmppserverlog.error(f"xml parse error - {newdata} - {e}")
                else:
                    self.send("</stream:stream>")  # Close stream

            else:
                if "<stream:stream" in newdata:  # Handle start stream and connect
                    if self.state == self.CONNECT or self.state == self.INIT:
                        xmppserverlog.debug(f"Handling connect data - {newdata}")
                        self._handle_connect(newdata.encode("utf-8"))
                else:
                    if "</stream:stream>" not in newdata:
                        xmppserverlog.error(f"xml parse error - {newdata} - {e}")
                    else:
                        self.send("</stream:stream>")  # Close stream
                        self.set_state("DISCONNECT")

        except Exception as e:
            xmppserverlog.exception(f"{e}")

    def _handle_iq(self, xml: ET.Element, data: str) -> None:

        if len(xml):
            child = self._tag_strip_uri(xml[0].tag)
        else:
            child = None

        if xml.tag == "iq":
            if child == "bind":
                self._handle_bind(xml)
            elif child == "session":
                self._handle_session(xml)
            elif child == "ping":
                self._handle_ping(xml)
            elif child == "query":
                if self.type == self.BOT:
                    self._handle_result(xml, data)
                else:
                    self._handle_ctl(xml, data)
            elif xml.get("type") == "result":
                if self.type == self.BOT:
                    self._handle_result(xml, data)
                else:
                    self._handle_result(xml, data)
            elif xml.get("type") == "set":
                if self.type == self.BOT:
                    self._handle_result(xml, data)
                else:
                    self._handle_result(xml, data)
