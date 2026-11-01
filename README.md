# Hospital Reach Map

How far is help? Drive-time zones (10/20/30 min) to hospitals, ambulance stations and "medical deserts".
Stack: OpenStreetMap data + Amazon Location Service (maps + isolines) + AWS Amplify hosting.

## Folder
- `frontend/`  the website (index.html, config.js, data/)
- `scripts/`   build_data.py (makes the data), requirements.txt, manual_ambulance.json
- `amplify.yml` Amplify build settings (if you deploy from GitHub)

## Steps
1. **Amazon Location API key**: console > Amazon Location Service > API keys > Create (permission: Maps). Put region + key in `frontend/config.js`.
2. **Preview instantly (fake data):**
   `pip install -r scripts/requirements.txt`
   `python scripts/build_data.py --city karachi --demo`
   then in `frontend/`: `python -m http.server 8000` and open http://localhost:8000
3. **Real data:**
   `aws configure` (IAM user with `geo-routes:CalculateIsolines`)
   `python scripts/build_data.py --city karachi`
   If isolines fail, the script falls back to estimated radius zones and the UI labels this.
4. **Ambulance services missing in OSM?** (Edhi, Chhipa, Rescue 1122) add them to `scripts/manual_ambulance.json`:
   `[{"name": "Edhi Center ...", "lat": 24.9, "lon": 67.0}]` and re-run step 3.
5. **Deploy:** Amplify console > Create app > Deploy without Git > upload a zip of the `frontend/` folder contents.
   (Or push to GitHub and connect it; `amplify.yml` is included.)
6. **Lock the API key:** add your Amplify domain to the key's allowed referrers.

## Cleanup after the challenge
Delete the Amplify app, the Location API key, and (if used) any S3 buckets to avoid charges.
