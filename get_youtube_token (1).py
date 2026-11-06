"""
Run this ONCE on your own computer to let the bot upload to your channel.
It opens a browser, you log in and click Allow, and it prints a refresh token.
Needs the file client_secret.json (downloaded from Google Cloud) in the same folder.
"""
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

print("\n==== COPY THESE THREE VALUES INTO GITHUB SECRETS ====")
print("YT_CLIENT_ID     =", creds.client_id)
print("YT_CLIENT_SECRET =", creds.client_secret)
print("YT_REFRESH_TOKEN =", creds.refresh_token)
print("=====================================================")
print("Keep these private. Anyone with them can upload to your channel.")
