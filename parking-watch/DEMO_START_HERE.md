# READY-TO-RUN DEMO

On a Mac:
1. Clone or download the Parking Watch repository.
2. Open `parking-watch/START_HERE.command` (right-click > Open if macOS blocks the first launch).
3. It installs Python dependencies, starts the local app, and opens http://localhost:8000.
4. Add your `NACE_API_KEY` to the local `.env` and restart the app. Click **Check Nace connection**, then **Start camera + analysis** and grant webcam permission.
5. Present an SFMTA 3-wheeled enforcement vehicle photo on a phone for at least 2 frames; remove it to demonstrate the departure event.
6. NDI document parsing/extraction can be launched from the dashboard. Search needs relevant documents already available in your Nace workspace.

To send SMS, configure Twilio and a destination number in the local `.env`. Follow the setup steps in [README.md](README.md). Set `THREE_WHEEL_DEMO_ALERTS=true` to demonstrate SMS on the first plausible three-wheeler sighting.

The app binds to 127.0.0.1 by default. The dashboard displays the observation countdown separately from the manual parking deadline.

Live Nace service access, camera hardware and SMS delivery must be tested on the user's computer.
