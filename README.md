![Space Status](https://spaceapistatusimage.hosted.quelltext.eu/status?url=https%3A%2F%2Fmembers.pawprintprototyping.org%2Fapi%2Fspacedirectory%2F)

# Administrative Documents
Administrative resources for the org

# How to use this repository
This repository includes some automation to handle monthly agenda tasks. The
automation is handled by a GitHub Action that runs on the first of every month.

## Setup
```shell
cp config-template.json config.json
vi config.json
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Repository configuration
```shell
gh repo set-default YOUR_ORGANIZATION/admin
gh secret set TELEGRAM_BOT_TOKEN -b "YOUR_BOT_TOKEN"
gh secret set TELEGRAM_CHAT_ID -b "YOUR_CHAT_ID"
gh secret set REPO_BASE_URL -b "https://github.com/YOUR_ORGANIZATION/admin"
```

Then go to https://github.com/YOUR_ORGANIZATION/admin/settings/actions and change the workflow permissions to "Read and write permissions".

## Running the automation
It magically works on the first of every month.
