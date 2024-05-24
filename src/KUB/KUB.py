import copy
from datetime import datetime
import aiohttp
from enum import Enum

class KUBUtilityTypes(Enum):
    """KUB Utility Types"""

    ELECTRICITY = "E"
    GAS = "G"
    WATER = "W"
    WASTEWATER = "WW"

    @staticmethod
    def expand(utility_type) -> str:
        """Expand enum to string representation"""
        match utility_type:
            case KUBUtilityTypes.ELECTRICITY:
                return 'electricity'
            case KUBUtilityTypes.GAS:
                return 'gas'
            case KUBUtilityTypes.WATER:
                return 'water'

class Http:
    async def __aenter__(self):
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        return self

    async def __aexit__(self, *err):
        await self._session.close()
        self._session = None

    async def fetch(self, url):
        resp = await self._session.get(url)
        resp.raise_for_status()
        return resp
    
    async def post(self, url, payload):
        resp = await self._session.post(url,json=payload)
        resp.raise_for_status()
        return resp
        
class kubUtility:
   
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.personID = ""
        self.accountID = ""
        self.account = {}
        self.services = {}
        self.usage = { "electricity": {}, "gas": {}, "water": {} }
        self.hasSession = False
        self.session = aiohttp.ClientSession()
        self.http = Http()
    
    async def retrieve_access_token(self):
        payload = {}
        session_data = {}
        session_data['username'] = self.username
        session_data['password'] = self.password
        session_data['expirationDate'] = "null"
        session_data['user'] = "null"
        payload['session'] = session_data
        

        if (self.hasSession is False):
            url = "https://www.kub.org/api/auth/v1/sessions"
            await self.http.post(url, payload)
            self.hasSession = True

        

    async def retrieve_account_info(self):
        response = await self.http.fetch("https://www.kub.org/api/auth/v1/users/jrandolph")
        json = await response.json()
        self.personID = json['person'][0]['id']
        self.accountID = json['person'][0]['accounts'][0]
        await self._retrieve_services()

    async def _retrieve_services(self):
        #Do we have a valid session?
        await self.retrieve_access_token()

        #Have we retrieved account info?
        if len(self.personID) == 0:
            await self.retrieve_account_info()
            
        url = "https://www.kub.org/api/cis/v1/accounts/" + self.accountID + "?include=all"
        response = await self.http.fetch(url)
        json = await response.json()
        services = json['service-point']

        for service in services:
            match service['type']:
                case "E-RES":
                    self.account['electricity'] = service['id']
                case "G-RES":
                    self.account['gas'] = service['id']
                case "W/S-RES":
                    self.account['water'] = service['id']
                case _:
                    raise Exception("An unexpected service ID:", service['id'])

    async def retrieve_usage(self, utility_type, start_date:str = datetime.today().strftime("%Y-%m-%d"), end_date:str = datetime.today().strftime("%Y-%m-%d")):
        #Do we have a valid session?
        await self.retrieve_access_token()

        #Have we retrieved account info?
        if len(self.personID) == 0:
            await self.retrieve_account_info()

        start_date = datetime.today().replace(day=1).date().strftime("%Y-%m-%d")
        #end_date = datetime.today().strftime("%Y-%m-%d")
        utility = utility_type.name.lower()
        account = self.account[utility]

        url = "https://www.kub.org/api/ami/v1/usage-values" + \
              "?endDate=" + end_date + "&personId=" + self.personID + "&servicePointId=" + account + \
              "&startDate=" + start_date + "&utilityType=" + utility_type.value        

        response = await self.http.fetch(url)
        json = await response.json()
        total = 0.0
        date = ""
        usage_data = {}
        for idx, usage in enumerate(json['usage-value']):
            if (len(usage['usageValuesChildren']) == 0 ):
                #Pull data from the base object
                usage_data['id'] = usage['id']
                usage_data['readDateTime'] = usage['readDateTime']

                #Grab the usage object via index
                data = json['usage-aggregate'][idx]

                #Read data from the usage object
                usage_data['utilityUsed'] = data['readValue']
                usage_data['uom'] = data['uom']
                usage_data['cost'] = data['cost']

                #Create another object with key of time
                time = datetime.fromisoformat(usage['readDateTime']).strftime("%H:%M:%S")
                self.usage[utility][date][time] = {}

                #Apend all the data 
                self.usage[utility][date][time] = copy.deepcopy(usage_data)

                total = data['readValue'] + total
                #print(self.usage)
            else:
                #This is the aggregate case so create a new blank object in the list
                date = datetime.fromisoformat(usage['readDateTime']).strftime("%Y-%m-%d")
                self.usage[utility][date] = {}

        print(utility, ":", total, usage_data['uom'])
        return self.usage

    async def retrieve_all_usage(self):
        async with Http() as self.http:
            await self.retrieve_usage(KUBUtilityTypes.ELECTRICITY)
            await self.retrieve_usage(KUBUtilityTypes.GAS)
            await self.retrieve_usage(KUBUtilityTypes.WATER)
        self.http = None
        return self.usage
    
    async def retrieve_usage_by_datetime(self, usage_record: datetime = datetime.now()):
        await self.retrieve_all_usage()
        elec = self.usage.get('electricity').get(usage_record.today().replace(day=1).date().strftime("%Y-%m-%d")).get(datetime.now().strftime('%H:00:00'))
        gas = self.usage.get('gas').get(usage_record.today().replace(day=1).date().strftime("%Y-%m-%d")).get(datetime.now().strftime('%H:00:00'))
        water = self.usage.get('water').get(usage_record.today().replace(day=1).date().strftime("%Y-%m-%d")).get(datetime.now().strftime('%H:00:00'))
        return elec, gas, water

    async def get_available_services(self):
        if self.services is None:
             async with Http() as self.http:
                 await self._retrieve_services()