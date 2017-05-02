import sys
from typing import Any, MutableMapping, Optional, Type, cast
from ..exceptions import KeyDecodeError, ValueDecodeError
from ..types import K, V, Event, Message, ModelT, Request
from ..types.serializers import AsyncSerializerT, RegistryT
from ..utils.compat import want_bytes
from ..utils.imports import symbol_by_name
from .codecs import CodecArg, dumps, loads

_flake8_Any_is_really_used: Any  # XXX flake8 bug

__all__ = ['Registry']


class Registry(RegistryT):

    #: Mapping of serializers that needs to be async
    override_classes = {
        'avro': 'faust.serializers.avro.faust:AvroSerializer',
    }

    #: Async serializer instances are cached here.
    _override: MutableMapping[CodecArg, AsyncSerializerT] = None

    def __init__(self,
                 key_serializer: CodecArg = None,
                 value_serializer: CodecArg = 'json') -> None:
        self.key_serializer = key_serializer
        self.value_serializer = value_serializer
        self._override = {}

    async def loads_key(self, typ: Optional[Type], key: bytes) -> K:
        """Deserialize message key.

        Arguments:
            typ: Model to use for deserialization.
            key: Serialized key.
        """
        if key is None or typ is None:
            return key
        try:
            typ_serializer = typ._options.serializer
            serializer = typ_serializer or self.key_serializer
            try:
                ser = self._get_serializer(serializer)
            except KeyError:
                obj = loads(serializer, key)
            else:
                obj = await ser.loads(key)
            return cast(K, typ(obj))
        except Exception as exc:
            raise KeyDecodeError(str(exc)).with_traceback(sys.exc_info()[2])

    async def loads_value(self,
                          typ: Type,
                          key: K,
                          message: Message,
                          request: Request) -> Event:
        """Deserialize message value.

        Arguments:
            typ: Model to use for deserialization.
            key: Deserialized key.
            message: Message instance containing the serialized message body.
        """
        if message.value is None:
            return None
        try:
            obj: Any = None
            typ_serializer = typ._options.serializer
            serializer = typ_serializer or self.value_serializer
            try:
                ser = self._get_serializer(serializer)
            except KeyError:
                obj = loads(serializer, message.value)
            else:
                obj = await ser.loads(message.value)
            return cast(Event, typ(obj, req=request))
        except Exception as exc:
            raise ValueDecodeError(str(exc)).with_traceback(sys.exc_info()[2])

    async def dumps_key(self, topic: str, key: K,
                        serializer: CodecArg = None) -> Optional[bytes]:
        """Serialize key.

        Arguments:
            topic: The topic that the message will be sent to.
            key: The key to be serialized.
            serializer: Custom serializer to use if value is not a Model.
        """
        is_model = False
        if isinstance(key, ModelT):
            is_model, serializer = True, key._options.serializer

        if serializer:
            try:
                ser = self._get_serializer(serializer)
            except KeyError:
                if is_model:
                    return cast(ModelT, key).dumps()
                return dumps(serializer, key)
            else:
                return await ser.dumps_key(topic, cast(ModelT, key))

        return want_bytes(cast(bytes, key)) if key is not None else None

    async def dumps_value(self, topic: str, value: V,
                          serializer: CodecArg = None) -> Optional[bytes]:
        """Serialize value.

        Arguments:
            topic: The topic that the message will be sent to.
            value: The value to be serialized.
            serializer: Custom serializer to use if value is not a Model.
        """
        is_model = False
        if isinstance(value, ModelT):
            is_model, serializer = True, value._options.serializer
        if serializer:
            try:
                ser = self._get_serializer(serializer)
            except KeyError:
                if is_model:
                    return cast(ModelT, value).dumps()
                return dumps(serializer, value)
            else:
                return await ser.dumps_value(topic, cast(ModelT, value))
        return cast(bytes, value)

    def _get_serializer(self, name: CodecArg) -> AsyncSerializerT:
        # Caches overridden AsyncSerializer
        # e.g. the avro serializer communicates with a Schema registry
        # server, so it needs to be async.
        # See Registry.dumps_key, .dumps_value, .loads_key, .loads_value,
        # and the AsyncSerializer implementation in
        #   faust/utils/avro/faust.py
        if not isinstance(name, str):
            raise KeyError(name)
        try:
            return self._override[name]
        except KeyError:
            ser = self._override[name] = (
                symbol_by_name(self.override_classes[name])(self))
            return cast(AsyncSerializerT, ser)
