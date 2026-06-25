# Introduction to Software Systems S26  
## Course Project: Identity-Verified Multiplayer Arena  

The assignment is available [here](https://cs6201.github.io/s26/assets/Project.pdf).

[This](https://hackmd.io/@iss-spring-2026/S1WBWzzoWe) is where you can ask questions about it, for which you will receive answers [here](https://hackmd.io/@iss-spring-2026/ryZ_WGzibx).

Good luck, have fun!

---

# Identity-Verified Multiplayer Arena
## Overview

Identity-Verified Multiplayer Arena is a full-stack real-time multiplayer gaming platform that replaces traditional password authentication with biometric facial verification. The system combines automated data harvesting, polyglot persistence using MySQL and MongoDB, secure authentication, WebSocket-based multiplayer communication, and an Elo-based ranking system.

### Key Features

* Facial-recognition-based authentication
* Polyglot persistence with MySQL and MongoDB
* Real-time multiplayer communication using WebSockets
* Multiplayer Tic-Tac-Toe with server-authoritative game state
* Dynamic Elo rating and leaderboard system
* Live lobby with player presence tracking
* Automated profile-image harvesting and storage pipeline

The project was developed as part of the Introduction to Software Systems course and demonstrates concepts spanning distributed systems, databases, networking, authentication, and full-stack application development.


## Phase 1: The Polyglot Harvester

---

### Database Schemas

#### MySQL
```sql
CREATE DATABASE IF NOT EXISTS arena;

USE arena;

CREATE TABLE IF NOT EXISTS users (
    uid VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255),
    elo_rating INT DEFAULT 1200,
    is_online BOOLEAN DEFAULT FALSE
);
```

#### MongoDB
No manual schema setup needed. The scraper automatically creates:
- Database: `arena`
- Collection: `profile_images`

Each document structure:
```json
{
    "uid": "student_uid",
    "image": "<binary image data>"
}
```

---

### Setup Instructions

#### 1. Clone the repo and install Python dependencies
```bash
uv sync
```
This reads `pyproject.toml` and installs all dependencies automatically.  
All teammates only need to run this,no manual `uv add` needed.

#### 2. Install system dependencies (required for `dlib` to compile)
```bash
sudo dnf install gcc gcc-c++ cmake python3-devel
```

#### 3. Set up MySQL
Start MySQL and run the following:
```bash
sudo systemctl start mysql
```
```sql
CREATE USER 'app_user'@'localhost' IDENTIFIED BY 'app_pass';
GRANT ALL PRIVILEGES ON arena.* TO 'app_user'@'localhost';
FLUSH PRIVILEGES;

CREATE DATABASE IF NOT EXISTS arena;
USE arena;
CREATE TABLE IF NOT EXISTS users (
    uid VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255),
    elo_rating INT DEFAULT 1200,
    is_online BOOLEAN DEFAULT FALSE
);
```

#### 4. Start MongoDB
```bash
sudo systemctl start mongod
```
No further setup needed.

---

### Running the Scraper
Make sure `batch_data.csv` is in the same directory as `scraper.py`, then:
```bash
uv run scraper.py
```

---

### Verifying the Data

#### MySQL
```bash
mysql -u app_user -p arena
```
```sql
SELECT * FROM users LIMIT 10;
SELECT COUNT(*) FROM users;
```

#### MongoDB
```bash
mongosh
```
```javascript
use arena
db.profile_images.countDocuments()
db.profile_images.find({}, {uid: 1, _id: 0})
```
Note: MongoDB count may be lower than MySQL,this is expected.  
MySQL has every student, MongoDB only has those with a valid image.

---

## Phase 3: The Synchronized Arena

### Running the Backend Server

The FastAPI server handles both HTTP routes and WebSocket connections.

Start the backend from the project root:

```bash
uv run uvicorn main:app --reload
```

If not using uv:

```bash
uvicorn main:app --reload
```

The server starts at:

```
http://localhost:8000
```

---

### HTTP Endpoints

Get all online users:

```
GET /users
```

Example:

```
http://localhost:8000/users
```

This endpoint returns all users where `is_online = TRUE`.

---

### WebSocket Service

Clients connect using:

```
ws://localhost:8000/ws/{uid}
```

Example:

```
ws://localhost:8000/ws/cs22b001
```

The WebSocket service provides:

* Live lobby presence updates  
* Match challenge requests  
* Challenge accept and decline  
* Disconnect detection  

---

### Running the Frontend

After login, open the dashboard page.  
The frontend automatically connects to the WebSocket server and renders the live lobby.

No additional service is required. The same FastAPI server handles HTTP and WebSockets.

---

### Development Notes

* Server must be running before opening the frontend  
* Multiple users can connect simultaneously  
* Lobby updates automatically without refresh  
* Clicking a user sends a real time challenge

## Contributors

* **Shradha Kedia**
* **Suravaram Pranay Reddy**
* **Suhaan Dabra**

