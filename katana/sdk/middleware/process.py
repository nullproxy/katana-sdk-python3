import asyncio
import inspect
import logging
import os

from concurrent.futures import CancelledError
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Process

import zmq.asyncio

from katana.sdk import serialization
from katana.sdk.component.request import Request
from katana.sdk.component.response import Response
from katana.sdk.component.transport import Transport
from katana.sdk.payload import CommandPayload
from katana.sdk.payload import ErrorPayload
from katana.sdk.payload import Payload
from katana.sdk.payload import ResponsePayload
from katana.sdk.payload import ServiceCallPayload
from katana.sdk.utils import install_uvevent_loop
from katana.sdk.utils import MultiDict

LOG = logging.getLogger(__name__)


class MiddlewareWorker(object):
    """Middleware worker class.

    This class handles middleware requests.

    A thread poll executor is used to allow concurrency in middlewares.

    """

    pool_size = 15

    def __init__(self, name, version, platform_version, callback,
                 channel, **kwargs):

        self.name = name
        self.version = version
        self.platform_version = platform_version
        self.callback = callback
        self.source_file = os.path.abspath(inspect.getfile(callback))
        self.worker_number = kwargs.get('worker_number')
        self.debug = kwargs.get('debug', False)
        self.channel = channel
        self.loop = asyncio.get_event_loop()
        self.context = zmq.asyncio.Context()

        # Only create the executor when callback is not a coroutine
        if not asyncio.iscoroutinefunction(self.callback):
            self.executor = ThreadPoolExecutor(self.pool_size)
        else:
            self.executor = None

    def _create_request_component_instance(self, payload):
        return Request(
            self.source_file,
            self.name,
            self.version,
            self.platform_version,
            payload.get('request/method'),
            payload.get('request/url'),
            # Keyword arguments
            protocol_version=payload.get('request/version'),
            query=MultiDict(payload.get('request/query')),
            headers=MultiDict(payload.get('request/headers')),
            post_data=MultiDict(payload.get('request/post_data')),
            body=payload.get('request/body'),
            files=payload.get('request/files'),
            service_name=payload.get('call/service'),
            service_version=payload.get('call/version'),
            action_name=payload.get('call/action'),
            action_params=MultiDict(payload.get('call/params')),
            debug=self.debug,
            )

    def _create_response_component_instance(self, payload):
        return Response(
            self.source_file,
            self.name,
            self.version,
            self.platform_version,
            # TODO: Get default status from gateway config (cli argument)
            '200 OK',
            Transport(payload.get('transport')),
            )

    def _create_component_instance(self, payload):

        command_name = payload.get('command/name')
        # Create a new payload using data in command arguments
        payload = Payload(payload.get('command/arguments'))
        if command_name == 'middleware-request':
            return self._create_request_component_instance(payload)
        elif command_name == 'middleware-response':
            return self._create_response_component_instance(payload)
        else:
            # TODO: Raise unknown command error
            pass

    @asyncio.coroutine
    def process_payload(self, payload):
        """Process a request payload.

        :param payload: A command payload.
        :type payload: CommandPayload.

        :returns: A Payload with the middleware response.
        :rtype: coroutine.

        """

        if not payload.path_exists('command'):
            # TODO: Return an error because a command payload was not sent
            pass

        # Check that command scope is gateway, otherwise is not valid
        if payload.get('meta/scope') != 'gateway':
            # TODO: Raise invalid scope error
            pass

        component = self._create_component_instance(payload)
        try:
            # TODO: Decorate callback to handle response types ...
            # Request, Response, mierda o nada
            if self.executor:
                # Execute middleware code in a different thread
                result = yield from self.loop.run_in_executor(
                    self.executor,
                    self.callback,
                    component,
                    )
            else:
                # Async call middleware callback
                result = yield from self.callback(component)
        except:
            LOG.exception('Middleware error')
            # Return an error payload
            # TODO
            raise NotImplementedError()

        # TODO: Move result to payload conversion to a callback decorator
        # Convert result to a payload
        if not result:
            raise NotImplementedError()

        if isinstance(result, Request):
            # Return a service call payload
            payload = ServiceCallPayload.new(
                service=result.get_service_name(),
                version=result.get_service_version(),
                action=result.get_action_name(),
                # TODO: Check that headers as always converted to dict
                params=list(result.get_action_params().items()),
                )
        elif isinstance(result, Response):
            # Return a response payload
            payload = ResponsePayload.new(
                version=result.get_protocol_version(),
                status=result.get_status(),
                body=result.get_body(),
                # TODO: Check that headers as always converted to dict
                headers=list(result.get_headers().items()),
                )
        else:
            # mierda
            # TODO: Create a response for the case where data is corrupt
            raise NotImplementedError()

        return payload.named()

    @asyncio.coroutine
    def handle_stream(self, stream):
        # Parse stream to get the commnd payload
        try:
            payload = CommandPayload(serialization.unpack(stream))
        except (ValueError, TypeError):
            # TODO: Return an error payload
            raise NotImplementedError()

        # Process command and return payload response serialized
        try:
            payload = yield from self.process_payload(payload)
        except:
            LOG.exception('Middleware payload process failed')
            # TODO: Return response for possible processing errors
            raise NotImplementedError()

        return serialization.pack(payload)

    @asyncio.coroutine
    def __call__(self):
        """Handle worker requests.

        :rtype: coroutine.

        """

        poller = zmq.asyncio.Poller()
        socket = self.context.socket(zmq.REP)
        socket.connect(self.channel)
        poller.register(socket, zmq.POLLIN)

        while True:
            events = yield from poller.poll()
            if dict(events).get(socket) == zmq.POLLIN:
                try:
                    # Get stream data from socket
                    stream = yield from socket.recv()
                    # Call request handler and send response back
                    response_stream = yield from self.handle_stream(stream)
                    yield from socket.send(response_stream)
                except CancelledError:
                    socket.close()
                    self.stop()
                    # Re raise exception to signal cancellation
                    raise

    def stop(self):
        """Terminates worker."""

        if self.executor:
            self.executor.shutdown()

        self.context.term()


class MiddlewareProcess(Process):
    """Middleware child process class.

    Each process initializes an event loop to run a given number
    of worker tasks.

    Each worker task is used to asynchronically handle service
    discovery commands.

    """

    def __init__(self, name, version, platform_version, channel, workers,
                 callback, *args, **kwargs):
        """Constructor.

        :param channel: IPC channel to connect to parent process.
        :type channel: str.
        :param workers: Number of middleware workers to start.
        :type workers: int.
        :param callback: A callable to use a request handler callback.
        :type callback: callable.

        """

        super().__init__(*args, **kwargs)
        self.name = name
        self.version = version
        self.platform_version = platform_version
        self.channel = channel
        self.workers = workers
        self.callback = callback

    def run(self):
        """Child process main code."""

        install_uvevent_loop()

        # Create an event loop for current process
        loop = zmq.asyncio.ZMQEventLoop()
        asyncio.set_event_loop(loop)

        # Create a task for each worker
        task_list = []
        for number in range(self.workers):
            worker = MiddlewareWorker(
                self.name,
                self.version,
                self.platform_version,
                self.callback,
                self.channel,
                worker_number=number,
                )
            task = loop.create_task(worker())
            task_list.append(task)

        try:
            loop.run_forever()
        except KeyboardInterrupt:
            pass
        finally:
            # Finish all tasks
            for task in task_list:
                loop.call_soon(task.cancel)

            # After tasks are cancelled close loop
            loop.call_soon(loop.close)
