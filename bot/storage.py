import logging
import os
import redis.asyncio as redis

logger = logging.getLogger(__name__)

class Storage:
    """Base Redis storage class for handling Redis operations."""

    def __init__(self):
        """Initialize the Storage class and set up the Redis connection."""
        self.redis_url = self._get_redis_url()
        self.storage = None
        self.last_id = '$'

    def _get_redis_url(self) -> str:
        """Construct and return the Redis URL from environment variables."""
        redis_host = os.getenv('REDIS_HOST', 'tradebot-redis-1')
        redis_port = os.getenv('REDIS_PORT', '6379')
        return f'redis://{redis_host}:{redis_port}?decode_responses=True&protocol=3'

    def _get_connection(self) -> redis.Redis:
        """Establish and return a Redis connection."""
        if self.storage is None:
            self.storage = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=10
            )
        return self.storage

    async def add_to_stream(self, name: str, stream_name: str, data: dict) -> str:
        """Add data to a Redis stream."""
        try:
            redis = self._get_connection()
            message_id = await redis.xadd(stream_name, {str(name): str(data)})
            logger.debug(f'Message {message_id} added to Redis stream {stream_name} by {name}')
            return message_id
        except Exception as e:
            logger.error(f'Unexpected error while add_to_stream {name} {stream_name} {name}: {e}')
            raise

    async def read_from_stream(self, stream_name: str, count: int, block: int) -> dict:
        """Read data from a Redis stream."""
        try:
            redis = self._get_connection()
            data = await redis.xread(streams={stream_name: self.last_id}, count=count)
            if data:
                stream, messages = data[0]
                messages_dict = {msg_id: msg_data for msg_id, msg_data in messages}
                # update last_id with the latest message ID
                self.last_id = messages[-1][0]
                await redis.xack(stream_name, 'None', *messages_dict.keys())
                logger.debug(f'Messages read from Redis stream {stream_name} now acknowledged: {messages_dict}')
                return messages_dict
            return {}
        except Exception as e:
            logger.error(f'Unexpected error while read_from_stream {stream_name} {count} {block}: {e}')
            raise

