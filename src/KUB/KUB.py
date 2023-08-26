import requests, copy, pickle
from datetime import datetime

class KUB:
    electricity = "E"
    water  = "W"
    gas    = "G"
    wastewater = "WW"
    
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.personID = ""
        self.accountID = ""
        self.session  = requests.Session()
        self.account = {}
        self.usage = { "electricity": {}, "gas": {}, "water": {} }
        self.hasSession = False
    
    def retrieve_access_token(self):
        payload = {}
        session = {}
        session['username'] = self.username
        session['password'] = self.password
        session['expirationDate'] = "null"
        session['user'] = "null"
        payload['session'] = session

        if (self.hasSession == False):
            try:
                with open('session', 'r') as file:
                    self.session = pickle.load(file)
            except:
                url = "https://www.kub.org/api/auth/v1/sessions"
                #Get Access Token Cookie
                self.session.post(url,json=payload)
                with open('session', 'wb') as file:
                    pickle.dump(self.session, file)
                self.hasSession = True

    def retrieve_account_info(self):
        response = self.session.get("https://www.kub.org/api/auth/v1/users/jrandolph")
        json = response.json()
        self.personID = json['person'][0]['id']
        self.accountID = json['person'][0]['accounts'][0]
        self.retrieve_services()

    def retrieve_services(self):
        url = "https://www.kub.org/api/cis/v1/accounts/" + self.accountID + "?include=all"
        response = self.session.get(url)
        json = response.json()
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

    def retrieve_usage(self, utilityType):
        #Do we have a valid session?
        self.retrieve_access_token()

        #Have we retrieved account info?
        if len(self.personID) == 0:
            self.retrieve_account_info()

        date = datetime.today().replace(day=1).date().strftime("%Y-%m-%d")
        end_date = datetime.today().strftime("%Y-%m-%d")
        match utilityType:
            case KUB.electricity:
                account = self.account['electricity']
                utility = "electricity"
            case KUB.gas:
                account = self.account['gas']
                utility = "gas"
            case KUB.water:
                account = self.account['water']
                utility = "water"

        url = "https://www.kub.org/api/ami/v1/usage-values" + \
              "?endDate=" + end_date + "&personId=" + self.personID + "&servicePointId=" + account + \
              "&startDate=" + date + "&utilityType=" + utilityType        

        response = self.session.get(url)
        json = response.json()
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
                usage_data['electricityUsed'] = data['readValue']
                usage_data['uom'] = data['uom']
                usage_data['cost'] = data['cost']

                #Create another object with key of time
                time = datetime.fromisoformat(usage['readDateTime']).strftime("%H:%M:%S")
                self.usage[utility][date][time] = {}

                #Apend all the data 
                self.usage[utility][date][time] = copy.deepcopy(usage_data)

                total = data['readValue'] + total
            else:
                #This is the aggregate case so create a new blank object in the list
                date = datetime.fromisoformat(usage['readDateTime']).strftime("%Y-%m-%d")
                self.usage[utility][date] = {}

        print(utility, ":", total, usage_data['uom'])
        return self.usage

    def retrieve_all_usage(self):
        self.retrieve_usage(KUB.electricity)
        self.retrieve_usage(KUB.gas)
        self.retrieve_usage(KUB.water)
        return self.usage