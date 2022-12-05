from datetime import datetime, timedelta
from typing import Callable
from resources.commands import new_command
import hikari
from motor.motor_asyncio import AsyncIOMotorClient
from redis import asyncio as redis
import asyncio
from typing import Any, Optional
from inspect import iscoroutinefunction
import logging
import importlib
import snowfin # FIXME: temporary

logger = logging.getLogger()

from .secrets import MONGO_URL, REDISHOST, REDISPORT, REDISPASSWORD, DISCORD_APPLICATION_ID, DISCORD_TOKEN
from .models import UserData, GuildData, MISSING

instance: 'Bloxlink' = None

class Bloxlink(hikari.RESTBot):
    def __init__(self, *args, **kwargs):
        global instance

        super().__init__(*args, **kwargs)

        self.mongo: AsyncIOMotorClient = AsyncIOMotorClient(MONGO_URL); self.mongo.get_io_loop = asyncio.get_running_loop
        self.redis: redis.Redis = redis.Redis(host=REDISHOST, port=REDISPORT, password=REDISPASSWORD, decode_responses=True)

        self.started_at = datetime.utcnow()
        # self.http = snowfin.http.HTTP(DISCORD_APPLICATION_ID, token=DISCORD_TOKEN)

        instance = self
        # self.cache = benedict(keypath_separator=":")

    @property
    def uptime(self) -> timedelta:
        return datetime.utcnow() - self.started_at

    async def fetch_item(self, domain: str, constructor: Callable, item_id: str, *aspects) -> object:
        """
        Fetch an item from local cache, then redis, then database.
        Will populate caches for later access
        """
        # should check local cache but for now just fetch from redis

        if aspects:
            item = await self.redis.hmget(f"{domain}:{item_id}", *aspects)
            item = {x: y for x, y in zip(aspects, item) if y is not None}
        else:
            item = await self.redis.hgetall(f"{domain}:{item_id}")

        if not item:
            item = await self.mongo.bloxlink[domain].find_one({"_id": item_id}, {x:True for x in aspects}) or {"_id": item_id}

            if item and not isinstance(item, (list, dict)):
                if aspects:
                    items = {x:item[x] for x in aspects if item.get(x) and not isinstance(item[x], dict)}
                    if items:
                        await self.redis.hset(f"{domain}:{item_id}", items)
                else:
                    await self.redis.hset(f"{domain}:{item_id}", item)

        if item.get("_id"):
            item.pop("_id")

        item["id"] = item_id

        return constructor(**item)

    async def update_item(self, domain: str, item_id: str, **aspects) -> None:
        """
        Update an item's aspects in local cache, redis, and database.
        """
        # update redis cache
        await self.redis.hmset(f"{domain}:{item_id}", aspects)

        # update database
        await self.mongo.bloxlink[domain].update_one({"_id": item_id}, {"$set": aspects})

    async def fetch_user(self, user_id: str, *aspects) -> UserData:
        """
        Fetch a full user from local cache, then redis, then database.
        Will populate caches for later access
        """
        return await self.fetch_item("users", UserData, user_id, *aspects)

    async def fetch_guild_data(self, guild_id: str, *aspects) -> GuildData:
        """
        Fetch a full guild from local cache, then redis, then database.
        Will populate caches for later access
        """
        return await self.fetch_item("guilds", GuildData, guild_id, *aspects)

    async def update_user_data(self, user_id: str, **aspects) -> None:
        """
        Update a user's aspects in local cache, redis, and database.
        """
        return await self.update_item("users", user_id, **aspects)

    async def update_guild_data(self, guild_id: str, **aspects) -> None:
        """
        Update a guild's aspects in local cache, redis, and database.
        """
        return await self.update_item("guilds", guild_id, **aspects)

    async def fetch_roles(self, guild_id: str | int) -> dict[str, dict]:
        """
        Fetch the guild's roles. Not cached.
        """

        r = snowfin.http.Route(
            "GET",
            "/guilds/{guild_id}/roles",
            guild_id=guild_id,
            auth=True
        )

        return {r["id"]: r for r in await self.http.request(r)} # so we can do fast ID lookups

    async def create_role(self, guild_id: str | int, name: str, reason: str = "") -> dict[str, dict]:
        """
        Create a new role. Not cached.
        """

        r = snowfin.http.Route(
            "POST",
            "/guilds/{guild_id}/roles",
            guild_id=guild_id,
            auth=True
        )

        data = {
            "name": name
        }

        return await self.http.request(r, data=data, headers={"X-Audit-Log-Reason": reason or ""})

    async def delete_role(self, guild_id: str | int, role_id: str | int, reason: str = "") -> dict[str, dict]:
        """
        Deletes a role.
        """

        r = snowfin.http.Route(
            "DELETE",
            "/guilds/{guild_id}/roles/{role_id}",
            guild_id=guild_id,
            role_id=role_id,
            auth=True
        )

        return await self.http.request(r, headers={"X-Audit-Log-Reason": reason or ""})

    async def edit_user(self, member: snowfin.Member, guild_id: str | int, *, roles: Optional[list] = MISSING, nick: Optional[str] = MISSING, mute: Optional[bool] = MISSING, deaf: Optional[bool] = MISSING, reason: str = "") -> Any:
        """
        Edit a member's roles and mute/deaf status.
        """

        r = snowfin.http.Route(
            "PATCH",
            "/guilds/{guild_id}/members/{user_id}",
            guild_id=guild_id,
            user_id=member.user.id,
            auth=True,
        )

        data = {}

        if roles is not MISSING:
            data["roles"] = roles

        if nick is not MISSING:
            data["nick"] = nick

        if mute is not MISSING:
            data["mute"] = mute

        if deaf is not MISSING:
            data["deaf"] = deaf

        if not data:
            raise RuntimeError("edit_user() requires at least one aspect to be changed")

        return await self.http.request(r, data=data, headers={"X-Audit-Log-Reason": reason or ""})

    async def edit_user_roles(self, member: snowfin.Member, guild_id: str | int, *, add_roles: list = None, remove_roles: list=None, reason: str = "") -> Any:
        """
        Adds or remove roles from a member.
        """

        new_roles = [r for r in member.roles if r not in remove_roles] + list(add_roles)

        return await self.edit_user(member, guild_id, roles=new_roles, reason=reason or "")

    @staticmethod
    def load_module(import_name: str) -> None:
        try:
            module = importlib.import_module(import_name)

        except (ImportError, ModuleNotFoundError) as e:
            logger.error(f"Failed to import {import_name}: {e}")
            raise e

        except Exception as e:
            logger.error(f"Module {import_name} errored: {e}")
            raise e

        if hasattr(module, "__setup__"):
            try:
                if iscoroutinefunction(module.__setup__):
                    asyncio.run(module.__setup__())
                else:
                    module.__setup__()

            except Exception as e:
                logger.error(f"Module {import_name} errored: {e}")
                raise e

        logging.info(f"Loaded module {import_name}")

    @staticmethod
    def command(**command_attrs):
        def wrapper(*args, **kwargs):
            return new_command(*args, **kwargs)

        return wrapper
