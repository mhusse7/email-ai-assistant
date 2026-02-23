# Adding Notion API Keys on Coolify

1. Go to your Coolify dashboard and select this project.
2. In the left sidebar, click on **"Environment Variables"**
3. Add a new variable named `NOTION_API_KEY` and paste the "Internal Integration Token" you got from Notion.
4. Add another variable named `NOTION_DATABASE_ID` and paste the 32-character string from your Notion database URL.
5. Click **"Save"** and wait for Coolify to redeploy your containers with the new keys!

If these are not provided, the Assistant will still work perfectly, but the Notion feature will be skipped automatically.
