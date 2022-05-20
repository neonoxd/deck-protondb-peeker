import logging
from aiohttp import ClientSession
from injector import inject_to_tab, get_tab, tab_has_element
import asyncio
import json
import os
import datetime

LOG_LOCATION = "/home/deck/pdb.log"
SETTINGS_DIR = "/home/deck/.config/pdbp"
CACHE_LOCATION = os.path.join(SETTINGS_DIR, "cache")
CFG_FILE = os.path.join(SETTINGS_DIR, "config.json")

logging.basicConfig(
    filename=LOG_LOCATION,
    format='%(asctime)s %(levelname)s %(message)s',
    filemode='w',
    force=True)

console = logging.StreamHandler()
console.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(name)-12s: %(levelname)-8s %(message)s')
console.setFormatter(formatter)
logging.getLogger('').addHandler(console)

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _ensure_dir(path):
    if not os.path.isdir(path) and not os.path.isfile(path):
        logger.info(f"{path} is not a directory, attempting to create it")
        os.makedirs(path)


def _init_cache():
    _ensure_dir(CACHE_LOCATION)


def _init_config():
    _ensure_dir(SETTINGS_DIR)
    logger.info(f"settings dir {SETTINGS_DIR} found")
    if os.path.isfile(CFG_FILE):
        logger.info(f"trying to load config file {CFG_FILE}")
        with open(CFG_FILE, mode="r") as f:
            return json.load(f)
    else:
        _save_config({"injectEnabled": False})
        return {"injectEnabled": False}


def _save_config(config):
    with open(CFG_FILE, mode="w") as f:
        json.dump(config, f)


def _save_cache(appid, response, data_type):
    if data_type in ["pc", "steam-deck", "summary", "metadata"]:
        target_filename = f"{appid}_{data_type}.json"
    else:
        target_filename = "counts.json"
    response["cache_date"] = datetime.datetime.now().timestamp()
    with open(os.path.join(CACHE_LOCATION, target_filename), "w") as f:
        logger.info(f"CACHE WRITE [{appid}] - [{data_type}]")
        json.dump(response, f)


def _read_cache(appid, data_type):
    logger.info(f"CACHE LOOKUP: [{appid}] - [{data_type}] ")

    if data_type in ["pc", "steam-deck", "summary", "metadata"]:
        target_filename = f"{appid}_{data_type}.json"
    else:
        target_filename = "counts.json"

    target_path = os.path.join(CACHE_LOCATION, target_filename)
    if os.path.isfile(target_path):
        logger.info(f"CACHE READ: [{target_path}]")
        with open(target_path, mode="r") as f:
            cached_json = json.load(f)
            jsondate = datetime.datetime.fromtimestamp(cached_json["cache_date"])
            diff = (datetime.datetime.now() - jsondate)
            logger.info(f"cache is {diff.days} days old")
            if diff.days == 0:
                return cached_json
            else:
                return None
    else:
        logger.info("NO CACHE")
        return None


class Plugin:

    async def find_appid_on_sp(self) -> str:
        logger.info("looking for appid...")
        scriptResult = await inject_to_tab("SP",
                    f"""
                    (function() {{
                        console.log("getAppId");
                        return document.body.innerHTML.match("/assets/[0-9]+_library")[0].replace("/assets/","").replace("_library","");
                    }})()
                    """, False)
        return scriptResult["result"]["result"]["value"]

    async def get_game_name(self, appId) -> str:
        logger.info(f"called get_game_name {appId}")
        cached = _read_cache(appId, "metadata")
        if cached is not None:
            return cached[f"{appId}"]["data"]["name"]
        else:
            async with ClientSession() as client:
                url = f"https://www.protondb.com/proxy/steam/api/appdetails/?appids={appId}"
                logger.info(f"HTTP GET @ {url}")
                async with client.get(url) as res:
                    if res.status == 200:
                        text = await res.text()
                        resp_json = json.loads(text)
                        if resp_json[f"{appId}"]["success"]:
                            _save_cache(appId, resp_json, "metadata")
                            return resp_json[f"{appId}"]["data"]["name"]

    async def get_app_summary(self, appId) -> str:
        logger.info(f"called get_app_summary {appId}")
        cached = _read_cache(appId, "summary")
        if cached is not None:
            return json.dumps(cached)
        else:
            async with ClientSession() as client:
                url = f"https://www.protondb.com/api/v1/reports/summaries/{appId}.json"
                logger.info(f"HTTP GET @ {url}")
                async with client.get(url) as res:
                    if res.status == 200:
                        text = await res.text()
                        summaries_json = json.loads(text)
                        _save_cache(appId, summaries_json, "summary")
                        return text
                    logger.info("not 200")
                return ""

    async def get_current_inject_config(self) -> bool:
        return self.config["injectEnabled"]

    async def set_inject(self, state):
        logger.info(f"setting if we should inject to game info page to [{state}]")
        self.config["injectEnabled"] = state
        _save_config(self.config)
        return state

    async def main_loop(self) -> str:
        while True:
            try:
                if self.config["injectEnabled"] and not await tab_has_element("SP", "proton-rating-tester"):
                    logger.info("no proton-rating-tester element, checking if we can inject")
                    compatlabelIsNullScriptRes = await inject_to_tab("SP",
                    f"""
                    (function() {{
                        console.log("compatlabelIsNullScriptRes");
                        return document.querySelector("[class^='appdetailsgameinfopanel_CompatLabel_']") !== null;
                    }})()
                    """, False)
                    canWeInject = compatlabelIsNullScriptRes["result"]["result"]["value"]
                    logger.info(f'canWeInject: {canWeInject}')
                    if canWeInject:
                        logger.info("we can inject, trying to do...")
                        await inject_to_tab("SP",
                        f"""
                        (function() {{
                            console.log("injectTester");
                            const elem = document.createElement('div');
                            elem.id = "proton-rating-tester";
                            document.querySelector("[class^='appdetailsgameinfopanel_CompatLabel_']").append(elem);
                        }})()
                        """, False)
                        logger.info("getting appid")
                        appId = await self.find_appid_on_sp(self)
                        logger.info("getting summaries")
                        sums = await self.get_app_summary(self, appId)
                        summaries = json.loads(sums)
                        logger.info(f"got summaries: {summaries}")
                        tier=summaries["tier"]

                        await inject_to_tab("SP",
                        f"""
                        (function() {{
                            console.log("injectProtonRatingBox");
                            let pstyle=`<style>
                            .rate_platinum {{
                                background-color: rgb(180, 199, 220);
                                color: black;
                            }}
                            .rate_gold {{
                                background-color: rgb(207, 181, 59);
                                color: black;
                            }}
                            .rate_silver {{
                                background-color: rgb(166, 166, 166);
                                color: black;
                            }}
                            .rate_bronze {{
                                background-color: rgb(205, 127, 50);
                                color: black;
                            }}
                            .rate_borked {{
                                background-color: red;
                                color: black;
                            }}
                            .pdb_rating {{
                                color: black;
                                letter-spacing: 2px;
                                line-height: 27px;
                                font-weight: 450;
                                text-align: center;
                                float:right;
                                text-transform: uppercase;
                            }}
                            </style>
                            `

                            let pstuff = `
                            <a href="https://www.protondb.com/app/{appId}" id="proton-rating" class="rate_{tier}">
                                <img alt="ProtonDB Logo" style="float: left;" height="25" src="https://www.protondb.com/sites/protondb/images/site-logo.svg" width="23">
                                <div class="pdb_rating">{tier}</div>
                            </a>
                            `

                            document.querySelector("[class^='appdetailsgameinfopanel_CompatLabel_']").outerHTML+=pstyle+pstuff;
                            

                        }})()
                        """, False)

            except Exception as e:
                logger.error(e)
            await asyncio.sleep(2)

    async def _main(self):
        logger.info("main called")
        self.config = _init_config()
        _init_cache()
        asyncio.get_event_loop().create_task(self.main_loop(self))
