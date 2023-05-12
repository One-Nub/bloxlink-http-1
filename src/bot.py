from os import environ as env, listdir
from resources.constants import MODULES
from config import SERVER_HOST, SERVER_PORT
from resources.secrets import DISCORD_PUBLIC_KEY, DISCORD_TOKEN
from resources.bloxlink import Bloxlink
from resources.commands import handle_command, sync_commands, handle_component, handle_autocomplete
import logging
import hikari
import fastapi
import uvicorn


logger = logging.getLogger()
logging.basicConfig(level=logging.INFO)


bot = Bloxlink(
    public_key=DISCORD_PUBLIC_KEY,
    token=DISCORD_TOKEN,
    token_type=hikari.TokenType.BOT,
    asgi_managed=False
)

app = fastapi.FastAPI(on_startup=[bot.start, sync_commands(bot)], on_shutdown=[bot.close])

bot.interaction_server.set_listener(hikari.CommandInteraction, handle_command)
bot.interaction_server.set_listener(hikari.ComponentInteraction, handle_component)
bot.interaction_server.set_listener(hikari.AutocompleteInteraction, handle_autocomplete)

for directory in MODULES:
    files = [name for name in listdir('src/'+directory.replace('.', '/')) if name[:1] != "." and name[:2] != "__" and name != "_DS_Store"]

    for filename in [f.replace(".py", "") for f in files]:
        if filename in ('bot', '__init__'):
            continue

        bot.load_module(f"{directory.replace('/','.')}.{filename}")

app.mount("/", bot) # blocks, no other sub-apps can be registered after

if __name__ == "__main__":
    uvicorn.run("bot:app", host=env.get("HOST", SERVER_HOST), port=env.get("PORT", SERVER_PORT), reload=True)
