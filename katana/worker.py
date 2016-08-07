import asyncio
import inspect
import logging
import os

from concurrent.futures import CancelledError
from concurrent.futures import ThreadPoolExecutor

import zmq.asyncio

from . import serialization
from .errors import HTTPError
from .payload import CommandPayload
from .payload import CommandResultPayload
from .payload import ErrorPayload

LOG = logging.getLogger(__name__)


class ComponentWorker(object):
    """Component worker task class.

    This class handles component requests.

    A thread poll executor is used to allow concurrency in components
    that are not implemented as coroutines.

    """

    pool_size = 15

    def __init__(self, callback, channel, cli_args):
        self.__socket = None

        self.callback = callback
        self.channel = channel
        self.cli_args = cli_args
        self.source_file = os.path.abspath(inspect.getfile(callback))
        self.loop = asyncio.get_event_loop()
        self.poller = zmq.asyncio.Poller()
        self.context = zmq.asyncio.Context()

        # Only create the executor when callback is not a coroutine
        if not asyncio.iscoroutinefunction(self.callback):
            self.executor = ThreadPoolExecutor(self.pool_size)
        else:
            self.executor = None

    @property
    def component_name(self):
        return self.cli_args['name']

    @property
    def component_version(self):
        return self.cli_args['version']

    @property
    def platform_version(self):
        return self.cli_args['platform_version']

    @property
    def debug(self):
        return self.cli_args['debug']

    def create_error_payload(self, exc, component):
        """Create a payload for the error response.

        :params exc: The exception raised in user land callback.
        :type exc: `Exception`
        :params component: The component being used.
        :type component: `Component`

        :returns: A result payload.
        :rtype: `Payload`

        """

        raise NotImplementedError()

    def create_component_instance(self, payload):
        """Create a component instance for a payload.

        The type of component created depends on the payload type.

        :param payload: A payload.
        :type payload: Payload.

        :raises: HTTPError

        :returns: A component instance for the type of payload.
        :rtype: `Component`.

        """

        raise NotImplementedError()

    def component_to_payload(self, command_name, component):
        """Convert callback result to a command result payload.

        :params command_name: Name of command being executed.
        :type command_name: str
        :params component: The component being used.
        :type component: `Component`

        :returns: A command result payload.
        :rtype: CommandResultPayload

        """

        raise NotImplementedError()

    @asyncio.coroutine
    def process_payload(self, payload):
        """Process a request payload.

        :param payload: A command payload.
        :type payload: `CommandPayload`

        :returns: A Payload with the component response.
        :rtype: coroutine.

        """

        if not payload.path_exists('command'):
            LOG.error('Payload missing command')
            return ErrorPayload.new('Internal communication failed').entity()

        # Check that command scope is gateway, otherwise is not valid
        if payload.get('meta/scope') != 'gateway':
            LOG.error('Unable to satisfy scope')
            return ErrorPayload.new('Internal communication failed').entity()

        command_name = payload.get('command/name')

        # Create a component instance using the command payload and
        # call user land callback to process it and get a response component.
        component = self.create_component_instance(payload)
        try:
            if self.executor:
                # Call callback in a different thread
                component = yield from self.loop.run_in_executor(
                    self.executor,
                    self.callback,
                    component,
                    )
            else:
                # Call callback asynchronusly
                component = yield from self.callback(component)
        except Exception as exc:
            LOG.exception('Component failed')
            payload = self.create_error_payload(
                exc,
                component,
                payload=payload,
                )
        else:
            payload = self.component_to_payload(payload, component)

        # Conver callback result to a command payload
        return CommandResultPayload.new(command_name, payload).entity()

    @asyncio.coroutine
    def handle_stream(self, stream):
        # Parse stream to get the commnd payload
        try:
            payload = CommandPayload(serialization.unpack(stream))
        except:
            LOG.exception('Invalid message format received')
            return serialization.pack(
                ErrorPayload.new('Internal communication failed').entity()
                )

        # Process command and return payload response serialized
        try:
            payload = yield from self.process_payload(payload)
        except HTTPError as err:
            payload = ErrorPayload.new(
                status=err.status,
                message=err.body,
                ).entity()
        except:
            LOG.exception('Component failed')
            payload = ErrorPayload.new().entity()

        return serialization.pack(payload)

    @asyncio.coroutine
    def _start_handling_requests(self):
        """Start handling incoming component requests and responses.

        This method starts an infinite loop that polls socket for
        incoming requests.

        """

        while True:
            events = yield from self.poller.poll()
            if dict(events).get(self.__socket) == zmq.POLLIN:
                # Get stream data from socket
                stream = yield from self.__socket.recv()
                # Call request handler and send response back
                response_stream = yield from self.handle_stream(stream)
                yield from self.__socket.send(response_stream)

    @asyncio.coroutine
    def __call__(self):
        """Handle worker requests.

        :rtype: coroutine.

        """

        self.__socket = self.context.socket(zmq.REP)
        self.__socket.connect(self.channel)
        self.poller.register(self.__socket, zmq.POLLIN)
        try:
            yield from self._start_handling_requests()
        except CancelledError:
            # Call stop before cancelling task
            self.stop()
            # Re raise exception to signal task cancellation
            raise

    def stop(self):
        """Terminates worker task."""

        self.poller.unregister(self.__socket)
        self.__socket.close()
        if self.executor:
            self.executor.shutdown()
