# CampusSnaps: build it in your own AWS account

One template builds the whole CampusSnaps app from the *From Laptop to Cloud* session. That's a photo-sharing website where students upload photos from campus events, browse them by event, and like them.

- **Time:** about 10 minutes (5 to deploy, 5 to explore)
- **Cost:** about 2 US cents an hour while it runs, paid from your Free Tier credits. Delete it when you're done (step 5).

## What gets built

```
 Students ──► EC2 website ──upload──► S3 bucket (uploads/)
                  ▲                        │ "a photo arrived" event
                  │ reads gallery,         ▼
                  │ adds likes        Lambda function
                  └──────────────── DynamoDB table ◄── saves photo details

 IAM roles give each part only the permissions it needs. No access keys anywhere.
```

| Decision | Service | What it does here |
|---|---|---|
| 1. Compute | **Amazon EC2** (t3.micro) | Runs the website, always on |
| 1. Compute | **AWS Lambda** | Runs only when a photo arrives, and saves its details |
| 2. Files | **Amazon S3** | Stores the photos. The bucket is private; photos are shown through 1-hour links |
| 3. Data | **Amazon DynamoDB** | One item per photo: event, caption, likes |
| 4. Access | **AWS IAM** | Two least-privilege roles, one for the server and one for the function |

It also creates a small private network (a VPC) with no NAT gateway, so the network itself costs nothing.

## 1. Before you start

- **An AWS account.** Either sign-up option works.
- **Your Region:**
    - **New sign-up:** AWS has already chosen your Region (Sydney for Sri Lanka). There's nothing to pick.
    - **Advanced (classic) sign-up:** choose **Asia Pacific (Mumbai)** in the Region menu at the top right of the console, and stay in that Region for every step.
- **The template file:** download `campussnaps.yaml` from this folder.

## 2. Deploy, option A: the CloudFormation console (no tools needed)

1. Open the **CloudFormation** console (type "CloudFormation" in the search bar at the top).
2. Choose **Create stack** → **With new resources (standard)**.
3. Choose **Upload a template file** → **Choose file**, pick `campussnaps.yaml`, then **Next**.
4. **Stack name:** `campussnaps`. Leave **InstanceType** as `t3.micro`. Choose **Next**.
5. On the options page, change nothing. Choose **Next**.
6. At the bottom, tick **"I acknowledge that AWS CloudFormation might create IAM resources"**. The template creates the two IAM roles. Then choose **Submit**.
7. Wait until the status says **CREATE_COMPLETE**, about 3–5 minutes. Use the refresh button to update it.
8. Open the **Outputs** tab. **WebsiteURL** is your app.

## 2. Deploy, option B: AWS CloudShell (one command)

1. Open **CloudShell** (the `>_` icon at the top of the console).
2. Choose **Actions** → **Upload file** and upload `campussnaps.yaml`.
3. Run:

```bash
aws cloudformation deploy \
  --template-file campussnaps.yaml \
  --stack-name campussnaps \
  --capabilities CAPABILITY_IAM

aws cloudformation describe-stacks --stack-name campussnaps \
  --query "Stacks[0].Outputs" --output table
```

## 3. Try it

1. Open the **WebsiteURL**.
    - Give the server **1–2 minutes** after CREATE_COMPLETE; it's still installing the app.
    - The address starts with `http://`, not `https://`. If your browser switches to https and fails, type `http://` yourself. The "Not secure" warning is expected for this demo.
2. Pick an event, add a caption, choose a photo (under 5 MB) and choose **Upload photo**.
3. The page says "Lambda is adding it to the gallery", then refreshes. Your photo appears a few seconds later. Choose the **heart** to like it.

## 4. Look under the hood

Each of these takes about a minute, and each one is a slide from the session.

| Open | Find | What it shows |
|---|---|---|
| **S3** → bucket `campussnaps-…` → `uploads/` | Your photo. Select it and open its **Object URL** | **Access Denied**: the bucket is private. The website shows photos through signed 1-hour links instead |
| **Lambda** → the function whose name contains `GalleryFunction` → **Monitor** → **View CloudWatch logs** | A line like `Saved 1793…-photo.jpg to event sports-day-2026` | Lambda ran for a fraction of a second, only because a photo arrived |
| **DynamoDB** → **Explore items** → the table whose name contains `PhotosTable` | One item per photo. `eventId` is the partition key, `photoId` the sort key | Like a photo on the website, refresh, and watch `likes` go up |
| **IAM** → **Roles** → the roles containing `WebServerRole` and `GalleryFunctionRole` | Their permissions | Each can do only its own job. There are no access keys anywhere |
| **EC2** → **Instances** → `campussnaps-web` → **Connect** → **Session Manager** | A terminal in your browser, with no SSH keys. Try `sudo journalctl -u campussnaps -n 20` | The website's own logs |

**Optional challenges:**

- **Least privilege:** in IAM, remove `dynamodb:PutItem` from the GalleryFunctionRole's policy and upload a photo. Find the `AccessDenied` error in the Lambda logs, then put the permission back.
- **Change the server:** choose **Update stack** with the same template and change **InstanceType** to `t8i.micro`, the newest Free Tier eligible generation. It's available in Mumbai.
- **Read the template:** open `campussnaps.yaml`. Every resource has a comment saying which decision it belongs to.

## 5. Clean up (do this every time)

**Console:** CloudFormation → select `campussnaps` → **Delete** → wait for **DELETE_COMPLETE**, about 3 minutes.

**CloudShell:**

```bash
aws cloudformation delete-stack --stack-name campussnaps
```

That deletes everything: the server, the network, the function, the table and the bucket, **including your photos**. A small helper in the template empties the bucket first, so nothing is left behind. Afterwards, check **Billing and Cost Management → Bills** to confirm nothing is still running.

## If something goes wrong

| What you see | What to do |
|---|---|
| The website doesn't load right after CREATE_COMPLETE | Wait 1–2 minutes and refresh. Make sure the address starts with `http://` |
| Still not loading after 5 minutes | EC2 → instance → **Connect** → **Session Manager**, then run `sudo tail -50 /var/log/cloud-init-output.log` |
| The photo uploads but never appears | Check the Lambda function's CloudWatch logs (step 4), then refresh the page |
| CREATE_FAILED: "campussnaps-… already exists" | A CampusSnaps stack already exists in this Region. Delete it first, or deploy in another Region |
| CREATE_FAILED on the instance type | That type isn't offered in your Region or Availability Zone. Deploy again with `t3.micro` |
| "Requires capabilities: [CAPABILITY_IAM]" | Tick the IAM acknowledgement box (console), or add `--capabilities CAPABILITY_IAM` (CloudShell) |
| DELETE_FAILED | Choose **Delete** again. If it still fails, open the **Events** tab to see which resource is stuck |

**Please note:** anyone with your WebsiteURL can upload photos, and the site uses plain HTTP. It's a learning demo, so don't upload private photos, and delete the stack when you're done.

## Files

| File | What it is |
|---|---|
| `campussnaps.yaml` | The CloudFormation template. This is the only file you deploy |
| `code/app.py` | The website that runs on EC2 (a readable copy of the code inside the template) |
| `code/gallery_function.py` | The Lambda function that saves photo details to DynamoDB |
| `code/empty_bucket.py` | The clean-up helper that empties the bucket when you delete the stack |
