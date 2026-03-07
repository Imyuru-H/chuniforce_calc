import requests


URL_BASE = "https://maimai.lxns.net/api/v0"
TOKEN = "JpNkyEWrtv2R8U6IJewJHaMfHHXg4D6bsTzqbWozc-o="

class ScoreSet(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(self, *args, **kwargs)

    def sort(self):
        pass

    def get_best_old_30(self):
        pass

class Requester:
    def __init__(self, token):
        self.headers = {"Authorization": token}
    
    def get_user_score(self):
        url = f"{URL_BASE}/user/chunithm/player/score"
        return requests.get(url=url, headers=self.headers)


if __name__ == "__main__":
    requester = Requester(TOKEN)
    print(requester.get_user_score().status_code)
    print(requester.get_user_score().content)