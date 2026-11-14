extends Node
## VICE EXCHANGE world bridge (autoload "ViceBridge").
##
## Turns this city into a 3D world client of the VICE EXCHANGE server. The server stays
## authoritative for the swarm, the market and capture; this script only renders what the
## server says and sends the same validated commands a browser client sends.
##
## Inert unless the game is launched with user args:   godot --path . -- --vice [options]
##   --vice-url=ws://127.0.0.1:3001/bridge   --vice-room=VICE-42
##   --vice-name=ACE   --vice-role=OUTLAW|HUNTER|SPECTATOR
##   --vice-windowed            keep the window (no exclusive fullscreen);  --vice-size=1600x900
##   --vice-time=16.9           hold the city clock at this hour (-1 lets it run)
##   --vice-keep-hud            keep the city's own single-player HUD on screen
##   --vice-look=neon|plain     neon (default): bloom, colour grade, neon trim on the district; plain: stock lighting
##   --vice-mode=scripted       room creator only: ask the server for drex | meta | scripted
##   --vice-shots=<dir>         verification: save PNG frames of the swarm in the city, then quit
##   --vice-check               verification (headless ok): join, receive ticks, walk to the mission, interact, quit

# ---------------------------------------------------------------- coordinates
# The server simulates a 120 x 80 m plane (x east, z south). Its road centres are at
# x = 32 / 62 / 92 and z = 28 / 54. This city's roads are on a 32 m grid, so one
# anisotropic affine transform lands sim roads on city roads and sim blocks on city blocks:
#     city.x = ANCHOR_CITY.x + (sim.x - ANCHOR_SIM.x) * SCALE.x
#     city.z = ANCHOR_CITY.y + (sim.z - ANCHOR_SIM.y) * SCALE.y
# (apps/server/src/bridge/README.md documents the same constants for the server side.)
const ANCHOR_SIM := Vector2(62.0, 28.0)
const ANCHOR_CITY := Vector2(112.0, -16.0)
const SCALE := Vector2(32.0 / 30.0, 32.0 / 26.0)
const SIM_MIN := Vector2(1.5, 1.5)      # just inside the server's walkable rectangle
const SIM_MAX := Vector2(118.5, 72.5)

const INTERACT_RADIUS := 2.5            # sim metres (server rule; the server re-checks)
const POSE_INTERVAL := 0.1
const ROLE_COLOR := {
	"WITNESS": Color("4ee1e6"), "INFORMANT": Color("b98cff"), "TRADER": Color("9be37a"),
	"POLICE": Color("6fa8ff"), "OUTLAW": Color("ff5fa8"), "HUNTER": Color("ffb347"),
}
const PINK := Color("ff4fa3")
const AQUA := Color("3fe0e6")
const TEXT := Color("f2eefc")
const DIM := Color("a79fc4")
const DANGER := Color("ff4d6d")
const GOOD := Color("7dffb0")

# ---------------------------------------------------------------- config / state
var active := false
var windowed := false
var url := "ws://127.0.0.1:3001/bridge"
var room_code := "VICE-42"
var nickname := "ACE"
var role := "OUTLAW"
var shots_dir := ""
var check_mode := false
var time_hours := 18.3          # neon dusk; the bridge holds the city's clock here (--vice-time=H, -1 = let it run)
var win_size := Vector2i(1600, 900)
var linger := 0.0               # check mode: stay connected this many seconds before quitting
var want_mode := ""             # room creator only: ask the server for drex | meta | scripted
var hunt := false               # check mode, HUNTER: shadow the outlaw and capture once it is wanted
var _hunt_last_shot := 0.0
var wait_before := 0.0          # check mode, OUTLAW: seconds to wait before walking to the job
var keep_city_hud := false      # --vice-keep-hud: leave the city's own single-player HUD on screen
var look := "neon"              # --vice-look=neon | plain  (plain keeps the city's stock lighting)
var record_dir := ""            # --vice-record=<dir>: save real-time JPEG frames + frames.txt for ffmpeg
var record_fps := 24.0
var _rec_t0 := -1.0
var _rec_last := -1.0
var _rec_n := 0
var _rec_list: PackedStringArray = []
var _intro_cam: Camera3D = null
var _cine_cam: Camera3D = null
var _cine_t := 0.0
var _cine_anchor := Vector3.ZERO
var _cine_look := Vector3.ZERO
var ambient := true             # --vice-no-ambient turns the extra background life off
var fx_shots := false           # --vice-fx (with --vice-shots): photograph an incident's message arc, ring and popup
var _last_arc: Dictionary = {}
var _fps_t := 0.0
var _look_done := false
var _fx: Array = []
var _fill_a: DirectionalLight3D
var _fill_b: DirectionalLight3D
const MSG_COLOR := {"REPORT": Color("ff4d6d"), "GOSSIP": Color("3fe0e6"), "RELAY": Color("b98cff"), "BULLETIN": Color("ffd166")}
const SOURCE_COLOR := {"drex": Color("7dfff0"), "meta": Color("b98cff"), "scripted": Color("c9c3dd"), "fallback": Color("ff8a8a"), "drex_low_confidence_guardrail": Color("ffc857"), "replay": Color("c9c3dd")}
var _win_fix := 90
var _created_room := false
var _key_down: Dictionary = {}
var _my_mission_done := false
var _trade_pressed := false
var _trade_balance0 := 0
var _respawn_pressed := false
var _respawn_done := false
var _old_character := ""
var _old_ticker := ""

var game = null                 # the upstream game node (scenes/main.tscn root)
var ws: WebSocketPeer = null
var ws_open := false
var joined := false
var session_token := ""
var _web_tuned := false
var udp_on := true               # --vice-no-udp: stay on the WebSocket for poses and ticks
var _udp = null                  # vice_udp.gd helper (UDP fast path), created when the server offers it
var stream_on := false           # --vice-stream: publish this game's frames to the server for the browser UI
var _stream = null               # vice_stream.gd helper
var web_embed := false         # true when running as the Web export inside the browser UI
var web_scale := 0.6           # 3D render scale in the Web export (0.4 to 1.0)
var my_player_id := ""
var my_character_id := ""
var my_role := ""
var state: Dictionary = {}
var have_state := false
var need_teleport := false
var ticks_seen := 0
var events_seen := 0
var pose_rejects := 0
var outside_sector := false

var _seq := 0
var _pending: Dictionary = {}   # n -> kind
var _reconnect_t := 0.0
var _pose_t := 0.0
var _last_pose := Vector2(-999, -999)
var _last_pose_sent_at := 0.0
var _prompt_action := ""        # "", "mission", "capture"
var _prompt_target := ""
var _notice := ""
var _notice_until := 0.0
var _banner_until := 0.0
var _feed: Array = []
var _prev_spot := -1
var _trend := ""
var _started_game := false
var _check_started_at := 0.0
var _check_stage := ""
var _shots_started := false
var _overview := false
var _captured_seen := false
var _captured_by := ""
var _wanted_shot := false
var _capture_shot := false
var _capture_at := 0.0
var _last_capture_try := 0.0

# --- guns, cars, helicopter (VICE mode) ---
var guns := true                # --vice-no-guns
var heli_on := true             # --vice-no-heli
var demo := ""                  # --vice-demo=gun|victim|car|watch|heli : scripted verification runs
var _demo_stage := ""
var _demo_t := 0.0
var _demo_p0 := Vector2.ZERO
var _last_fire := -10.0
var my_ammo := -1
var _my_stun_until := 0.0
var _stun_pos := Vector3.ZERO
var _stun_until: Dictionary = {}    # entity id -> local time the knock-down ends
var _tracers: Array = []
var _stuns_seen := 0
var _shots_seen := 0
var _heli = null

var entities: Dictionary = {}   # id -> { node, label, ring, pos, target, yaw, phase, kind, base, extra }
var beacons: Dictionary = {}    # mission id -> Node3D
var _free_cache: Dictionary = {}

var hud_layer: CanvasLayer
var hud_title: Label
var hud_main: Label
var hud_stats: Label
var hud_heat_row: HBoxContainer
var hud_heat_fill: ColorRect
var hud_heat_text: Label
var hud_feed: Label
var hud_prompt: Label
var hud_prompt_panel: PanelContainer
var hud_banner: Label
var hud_banner_sub: Label
var hud_banner_box: Control


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	var args: Array[String] = []
	for a in OS.get_cmdline_user_args(): args.append(a)
	if OS.has_feature("web"):
		# Embedded in the browser UI: settings come from the page URL, and the socket goes through the page's own origin.
		args.assign(["--vice", "--vice-windowed", "--vice-no-ambient"])  # the single-threaded web build needs the frame budget
		var q: Dictionary = {}
		for pair in str(JavaScriptBridge.eval("window.location.search.slice(1)", true)).split("&", false):
			var kv := pair.split("=")
			if kv.size() == 2: q[kv[0]] = str(kv[1]).uri_decode()
		var origin := str(JavaScriptBridge.eval("(location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host", true))
		if not q.has("url"): args.append("--vice-url=" + origin + "/bridge")
		for k in ["url", "name", "role", "room", "token", "time", "look", "scale"]:
			if q.has(k) and str(q[k]) != "": args.append("--vice-%s=%s" % [k, str(q[k])])
		web_embed = true
	for arg: String in args:
		if arg == "--vice": active = true
		elif arg.begins_with("--vice-token="): session_token = arg.trim_prefix("--vice-token=")
		elif arg == "--vice-windowed": windowed = true
		elif arg == "--vice-check": check_mode = true; active = true
		elif arg.begins_with("--vice-url="): url = arg.trim_prefix("--vice-url=")
		elif arg.begins_with("--vice-room="): room_code = arg.trim_prefix("--vice-room=").to_upper()
		elif arg.begins_with("--vice-name="): nickname = arg.trim_prefix("--vice-name=").substr(0, 16)
		elif arg.begins_with("--vice-role="): role = arg.trim_prefix("--vice-role=").to_upper()
		elif arg.begins_with("--vice-shots="):
			shots_dir = arg.trim_prefix("--vice-shots="); active = true; windowed = true
		elif arg.begins_with("--vice-time="): time_hours = float(arg.trim_prefix("--vice-time="))
		elif arg.begins_with("--vice-linger="): linger = float(arg.trim_prefix("--vice-linger="))
		elif arg.begins_with("--vice-mode="): want_mode = arg.trim_prefix("--vice-mode=").to_lower()
		elif arg == "--vice-hunt": hunt = true
		elif arg == "--vice-keep-hud": keep_city_hud = true
		elif arg == "--vice-no-ambient": ambient = false
		elif arg == "--vice-no-guns": guns = false
		elif arg == "--vice-no-udp": udp_on = false
		elif arg == "--vice-stream": stream_on = true
		elif arg == "--vice-no-heli": heli_on = false
		elif arg.begins_with("--vice-demo="):
			demo = arg.trim_prefix("--vice-demo="); active = true; windowed = true
		elif arg == "--vice-fx": fx_shots = true
		elif arg.begins_with("--vice-look="): look = arg.trim_prefix("--vice-look=")
		elif arg.begins_with("--vice-scale="): web_scale = clampf(float(arg.trim_prefix("--vice-scale=")), 0.4, 1.0)
		elif arg.begins_with("--vice-record="):
			record_dir = arg.trim_prefix("--vice-record="); windowed = true
		elif arg.begins_with("--vice-wait="): wait_before = float(arg.trim_prefix("--vice-wait="))
		elif arg.begins_with("--vice-size="):
			var wh: PackedStringArray = str(arg).trim_prefix("--vice-size=").split("x")
			if wh.size() == 2: win_size = Vector2i(int(wh[0]), int(wh[1]))
	if not active:
		set_process(false)
		return
	if not (role in ["OUTLAW", "HUNTER", "SPECTATOR"]): role = "OUTLAW"
	if shots_dir != "": DirAccess.make_dir_recursive_absolute(shots_dir)
	if record_dir != "": DirAccess.make_dir_recursive_absolute(record_dir)
	_apply_window()
	if web_embed:
		get_tree().root.scaling_3d_scale = web_scale  # WebGL 2 / Compatibility renderer: render 3D below native and upscale (page passes ?scale=)
		get_tree().root.msaa_3d = Viewport.MSAA_DISABLED
	process_priority = 50   # run after the city's own _process so a knock-down can hold the player still
	_build_hud()
	_check_started_at = _now()
	print("[vice] bridge active url=%s room=%s name=%s role=%s" % [url, room_code, nickname, role])


func _now() -> float:
	return Time.get_ticks_msec() / 1000.0

## The project boots in exclusive fullscreen; leaving it takes a few frames on macOS, so retry briefly.
func _apply_window() -> void:
	if not windowed or DisplayServer.get_name() == "headless": return
	if DisplayServer.window_get_mode() != DisplayServer.WINDOW_MODE_WINDOWED:
		DisplayServer.window_set_mode(DisplayServer.WINDOW_MODE_WINDOWED)
		return
	if DisplayServer.window_get_size() != win_size:
		DisplayServer.window_set_size(win_size)
		var scr := DisplayServer.screen_get_size()
		DisplayServer.window_set_position(Vector2i(maxi(0, (scr.x - win_size.x) / 2), maxi(0, (scr.y - win_size.y) / 2)))


# ---------------------------------------------------------------- transform helpers
func sim_to_city(x: float, z: float) -> Vector2:
	return Vector2(ANCHOR_CITY.x + (x - ANCHOR_SIM.x) * SCALE.x, ANCHOR_CITY.y + (z - ANCHOR_SIM.y) * SCALE.y)

func city_to_sim(gx: float, gz: float) -> Vector2:
	return Vector2(ANCHOR_SIM.x + (gx - ANCHOR_CITY.x) / SCALE.x, ANCHOR_SIM.y + (gz - ANCHOR_CITY.y) / SCALE.y)

func _ground(gx: float, gz: float) -> float:
	if game == null or game.world == null: return 0.0
	return game.world.surface_height(gx, gz)

## Nearest spot to (gx, gz) that the city's own collision test says is walkable.
func _free_spot(gx: float, gz: float, radius := 0.6) -> Vector2:
	if game == null or game.world == null: return Vector2(gx, gz)
	var key := Vector2i(roundi(gx * 2.0), roundi(gz * 2.0))
	if _free_cache.has(key): return _free_cache[key]
	var out := Vector2(gx, gz)
	if game.world.collides_at(gx, gz, radius):
		var found := false
		for ring in range(1, 16):
			var n := 8 + ring * 2
			for i in n:
				var a := float(i) / float(n) * TAU
				var x := gx + cos(a) * ring
				var z := gz + sin(a) * ring
				if not game.world.collides_at(x, z, radius):
					out = Vector2(x, z); found = true
					break
			if found: break
	if _free_cache.size() > 4000: _free_cache.clear()
	_free_cache[key] = out
	return out

func my_sim_pos() -> Vector2:
	if game == null: return Vector2.ZERO
	var p: Vector3 = game.player_pos
	return city_to_sim(p.x, p.z)


# ---------------------------------------------------------------- main loop
func _web_tune(node: Node) -> void:
	# Web build only: real-time shadows are the most expensive thing the Compatibility renderer does here.
	if node is Light3D: (node as Light3D).shadow_enabled = false
	for c in node.get_children(): _web_tune(c)


func _process(dt: float) -> void:
	if web_embed and not _web_tuned and game != null and Engine.get_frames_drawn() > 20:
		_web_tuned = true
		_web_tune(get_tree().root)
	if game == null:
		var scene := get_tree().current_scene
		if scene != null and "player_pos" in scene and "world" in scene and scene.world != null:
			game = scene
			if not check_mode or shots_dir != "" or record_dir != "" or demo != "": _start_game()
		else:
			return
	if _win_fix > 0:
		_win_fix -= 1
		_apply_window()
	if _started_game:
		_fps_t += dt
		if _fps_t > 5.0:
			_fps_t = 0.0
			print("[vice] fps=%d entities=%d fx=%d" % [int(Engine.get_frames_per_second()), entities.size(), _fx.size()])
		if not keep_city_hud and game.hud != null:
			# Our overlay replaces the city's single-player HUD; it comes back only for its death screen.
			game.hud.visible = float(game.player_hp) <= 0.0
		if time_hours >= 0.0: GameState.time_min = time_hours * 60.0
		GameState.wanted = 0.0          # the only law in this city is the swarm's police
	_pump_socket(dt)
	if _stream != null: _stream.poll(_now(), get_viewport())
	if _udp != null:
		if not ws_open: _udp.stop()
		var udp_tick: Dictionary = _udp.poll(_now())
		if not udp_tick.is_empty(): _on_tick(udp_tick)
	_poll_keys()
	if joined and have_state:
		if need_teleport: _teleport_to_server_position()
		_send_pose(dt)
		_update_entities(dt)
		_declutter_labels()
		_update_fx(dt)
		_update_weapons(dt)
		_update_prompts()
	_update_hud()
	_record_frame()
	if demo != "": _run_demo(dt)
	elif check_mode: _run_check()
	elif shots_dir != "" and joined and have_state and not _shots_started and entities.size() >= 8:
		_shots_started = true
		_run_shots()


## Skip the upstream boot screen and start a fresh run with our overlay.
func _start_game() -> void:
	if _started_game: return
	_started_game = true
	if game.hud != null: game.hud.visible = false
	game._on_start()
	if game.hud != null: game.hud.visible = true
	# The upstream suit pickup is not part of this demo: park it out of the world.
	if game.suit_node != null:
		game.suit_armed = false
		game.suit_node.visible = false
		game.suit_node.position = Vector3(0, -800, 0)
	GameState.weapon_idx = 2 if guns else 0   # pistol in hand: its trigger fires the server's stun pulse
	if game.has_method("_refresh_weapon_model"): game._refresh_weapon_model()
	_dress_pedestrians()
	if ambient: _populate_district()
	_place_heli()
	_apply_look()
	if time_hours >= 0.0: GameState.time_min = time_hours * 60.0
	_set_notice("Every character is a ticker. Survive to grow your value. The city decides for itself.", 8.0)
	if shots_dir != "":
		Input.mouse_mode = Input.MOUSE_MODE_VISIBLE


## Ambient pedestrians keep walking the streets, but only with the fully clothed rigs.
func _dress_pedestrians() -> void:
	var kinds := ["nathan", "vip_suit", "guard_suit", "nathan"]
	for n in game.npcs:
		var old = n.get("node")
		if old == null or not is_instance_valid(old): continue
		if str(old.get_meta("model_kind", "")) != "ubc": continue
		var fresh: Node3D = Human.build_model(kinds.pick_random())
		fresh.position = old.position
		fresh.rotation = old.rotation
		game.add_child(fresh)
		old.queue_free()
		n["node"] = fresh


## Background life for the mapped district: extra pedestrians (the city's own wandering AI moves
## them) and kerb-side cars. They carry no label, halo or marker, so they never read as swarm agents.
func _populate_district() -> void:
	var kinds := ["nathan", "vip_suit", "guard_suit", "nathan", "vip_suit"]
	var lo := sim_to_city(2.0, 2.0)
	var hi := sim_to_city(118.0, 72.0)
	var added := 0
	for i in 34:
		for tries in 12:
			var x := randf_range(lo.x, hi.x)
			var z := randf_range(lo.y, hi.y)
			if game.world.collides_at(x, z, 1.0): continue
			var node: Node3D = Human.build_model(kinds.pick_random())
			node.position = Vector3(x, 0, z)
			game.add_child(node)
			game.npcs.append({"node": node, "pos": Vector3(x, 0, z), "yaw": randf() * TAU, "hp": 35.0, "max_hp": 35.0,
				"walk_timer": 0.0, "walk_phase": randf() * TAU, "cash": 0})
			added += 1
			break
	var cars := 0
	if game.has_method("_make_vehicle"):
		# north-south streets: park at the kerb, clear of the centre line the police walk
		for rx in [80.0, 112.0, 144.0]:
			for mz in [-32.0, 0.0, 32.0]:
				if randf() < 0.25: continue
				var side := -1.0 if randf() < 0.5 else 1.0
				var cx: float = rx + side * 5.2
				var cz: float = mz + randf_range(-7.0, 7.0)
				if game.world.collides_at(cx, cz, 2.0): continue
				var v = game._make_vehicle(cx, cz, game.VEHICLE_COLORS.pick_random(), game.TRAFFIC_STYLES.pick_random())
				v.yaw = 0.0 if side > 0.0 else PI
				v.node.rotation.y = v.yaw
				game.vehicles.append(v)
				cars += 1
		for rz in [-16.0, 16.0]:
			for mx in [64.0, 96.0, 128.0, 160.0]:
				if randf() < 0.25: continue
				var side2 := -1.0 if randf() < 0.5 else 1.0
				var cx2: float = mx + randf_range(-7.0, 7.0)
				var cz2: float = rz + side2 * 5.2
				if game.world.collides_at(cx2, cz2, 2.0): continue
				var v2 = game._make_vehicle(cx2, cz2, game.VEHICLE_COLORS.pick_random(), game.TRAFFIC_STYLES.pick_random())
				v2.yaw = PI / 2.0 if side2 > 0.0 else -PI / 2.0
				v2.node.rotation.y = v2.yaw
				game.vehicles.append(v2)
				cars += 1
	print("[vice] background life: +%d pedestrians, +%d kerb-side cars" % [added, cars])


# ---------------------------------------------------------------- socket
func _pump_socket(dt: float) -> void:
	if ws == null:
		_reconnect_t -= dt
		if _reconnect_t > 0.0: return
		ws = WebSocketPeer.new()
		ws.inbound_buffer_size = 1 << 20
		ws.max_queued_packets = 4096
		var err := ws.connect_to_url(url)
		if err != OK:
			ws = null
			_reconnect_t = 2.0
		return
	ws.poll()
	var st := ws.get_ready_state()
	if st == WebSocketPeer.STATE_OPEN:
		if not ws_open:
			ws_open = true
			print("[vice] bridge connected")
			_send_join()
		while ws.get_available_packet_count() > 0:
			var pkt := ws.get_packet()
			if ws.was_string_packet():
				var msg = JSON.parse_string(pkt.get_string_from_utf8())
				if typeof(msg) == TYPE_DICTIONARY: _on_message(msg)
	elif st == WebSocketPeer.STATE_CLOSED:
		if ws_open: print("[vice] bridge disconnected (code %d)" % ws.get_close_code())
		ws = null
		ws_open = false
		joined = false
		_reconnect_t = 2.0
		_pending.clear()


func _send(cmd: Dictionary, kind := "") -> void:
	if ws == null or not ws_open: return
	_seq += 1
	if kind != "": _pending[_seq] = kind
	ws.send_text(JSON.stringify({"t": "cmd", "n": _seq, "cmd": cmd}))

func _cid(prefix: String) -> String:
	return "%s-%d-%d" % [prefix, Time.get_ticks_usec(), randi() % 100000]

func _send_join() -> void:
	# --vice-room=NEW creates a private room (used by the verification modes so they never touch the demo room).
	if room_code == "NEW":
		_created_room = true
		_send({"type": "room.create", "nickname": nickname, "role": role, "requestId": _cid("gjoin")}, "join")
		return
	var cmd := {"type": "room.join", "roomCode": room_code, "nickname": nickname, "role": role, "requestId": _cid("gjoin")}
	if session_token != "": cmd["sessionToken"] = session_token
	_send(cmd, "join")


func _on_message(msg: Dictionary) -> void:
	match str(msg.get("t", "")):
		"ack": _on_ack(msg)
		"tick":
			if _udp == null or not _udp.active: _on_tick(msg)   # UDP carries the world state when it is live
		"udp": _on_udp_offer(msg)
		"state": _on_state(msg)
		"event":
			var e = msg.get("e")
			if typeof(e) == TYPE_DICTIONARY: _on_event(e)


func _on_ack(msg: Dictionary) -> void:
	var n := int(msg.get("n", 0)) if msg.get("n") != null else 0
	var kind: String = _pending.get(n, "")
	_pending.erase(n)
	var ack: Dictionary = msg.get("ack", {})
	var ok: bool = bool(ack.get("ok", false))
	var err: Dictionary = ack.get("error", {}) if typeof(ack.get("error")) == TYPE_DICTIONARY else {}
	match kind:
		"join":
			if ok:
				var r: Dictionary = ack.get("result", {})
				joined = true
				session_token = str(r.get("sessionToken", ""))
				if stream_on and not web_embed and session_token != "":
					if _stream == null: _stream = load("res://scripts/autoload/vice_stream.gd").new()
					_stream.start(url, session_token)
				my_player_id = str(r.get("playerId", ""))
				my_character_id = str(r.get("characterId", "")) if r.get("characterId") != null else ""
				my_role = str(r.get("role", role))
				room_code = str(r.get("roomCode", room_code))
				need_teleport = true
				if want_mode != "" and bool(r.get("isDirector", false)):
					_send({"type": "demo.setMode", "mode": want_mode, "commandId": _cid("gmode")}, "mode")
				print("[vice] joined room=%s player=%s character=%s role=%s" % [str(r.get("roomCode", "")), my_player_id, my_character_id, my_role])
			else:
				_set_notice("JOIN FAILED: %s" % str(err.get("message", "?")), 6.0)
				print("[vice] join failed: ", err)
				# A private room does not survive a server restart: make a fresh one instead of retrying forever.
				if _created_room and str(err.get("code", "")) == "ROOM_NOT_FOUND":
					room_code = "NEW"
					session_token = ""
				if ws != null: ws.close()
		"pose":
			if not ok: pose_rejects += 1
		"mission":
			if ok:
				_set_notice("JOB DONE: notoriety up, heat up", 4.0)
				print("[vice] mission ack ok")
			else:
				_set_notice("MISSION: %s" % str(err.get("message", "rejected")), 3.0)
				print("[vice] mission rejected: ", err)
		"capture":
			if ok: _set_notice("CAPTURE CONFIRMED", 4.0)
			else: _set_notice("CAPTURE: %s" % str(err.get("reason", err.get("message", "rejected"))), 3.0)
		"respawn":
			if ok: need_teleport = true
			else: _set_notice("RESPAWN: %s" % str(err.get("message", "rejected")), 3.0)
		"incident":
			print("[vice] demo.incident -> %s" % ["ok" if ok else str(err)])
		"mode":
			print("[vice] demo.setMode %s -> %s" % [want_mode, "ok" if ok else str(err)])
		"weapon":
			if not ok:
				_last_fire = -10.0
				_set_notice("PULSE: %s" % str(err.get("message", err.get("reason", "rejected"))), 2.0)
				print("[vice] weapon.fire rejected: ", err)
		"trade":
			if ok: _set_notice("ORDER FILLED", 2.0)
			else: _set_notice("ORDER: %s" % str(err.get("reason", err.get("message", "rejected"))), 3.0)


func _on_state(msg: Dictionary) -> void:
	state = msg
	have_state = true
	var you = msg.get("you")
	if typeof(you) == TYPE_DICTIONARY:
		var cid := str(you.get("characterId", "")) if you.get("characterId") != null else ""
		if cid != my_character_id:
			my_character_id = cid
			need_teleport = true
		my_player_id = str(you.get("playerId", my_player_id))
		my_role = str(you.get("role", my_role))
	_sync_entities()
	_sync_beacons()
	var l := _my_listing()
	if not l.is_empty():
		var spot := int(l.get("spotPrice", 0))
		if _prev_spot >= 0 and spot != _prev_spot:
			_trend = "▲" if spot > _prev_spot else "▼"
		_prev_spot = spot


func _on_udp_offer(msg: Dictionary) -> void:
	# The server offers a UDP fast path after a join. Browsers have no UDP sockets, so the web build stays on WebSocket.
	if not udp_on or web_embed: return
	var h := url.get_slice("://", 1).get_slice("/", 0)
	if h.contains(":"): h = h.get_slice(":", 0)
	if _udp == null: _udp = load("res://scripts/autoload/vice_udp.gd").new()
	_udp.start(h, int(msg.get("port", 0)), str(msg.get("token", "")), int(msg.get("hz", 30)), _now())

func _on_tick(msg: Dictionary) -> void:
	ticks_seen += 1
	for c in msg.get("characters", []):
		var id := str(c.get("id", ""))
		if entities.has(id):
			var e: Dictionary = entities[id]
			var ns := Vector2(float(c.get("x", 0.0)), float(c.get("z", 0.0)))
			var tnow := _now()
			var dtt: float = tnow - float(e.last_t)
			if dtt > 0.05:
				e.speed = lerpf(float(e.speed), ns.distance_to(e.last_sim) / dtt, 0.5)
				e.last_sim = ns
				e.last_t = tnow
			e.sim = ns
			e.heat = float(c.get("heat", 0.0))
			e.status = str(c.get("status", ""))
	for n in msg.get("npcs", []):
		var nid := str(n.get("id", ""))
		if entities.has(nid):
			var en: Dictionary = entities[nid]
			en.sim = Vector2(float(n.get("x", 0.0)), float(n.get("z", 0.0)))
			en.activity = str(n.get("activity", ""))
			en.pending = bool(n.get("pending", false))


func _npc_name(id: String) -> String:
	for n in state.get("npcs", []):
		if str(n.get("id", "")) == id: return str(n.get("name", id))
	for p in state.get("players", []):
		if str(p.get("id", "")) == id: return str(p.get("nickname", id))
	return id

func _source_label(src) -> String:
	match str(src):
		"drex": return "DREX"
		"meta": return "META"
		"scripted": return "SCRIPTED"
		"fallback": return "FALLBACK"
		"drex_low_confidence_guardrail": return "DREX GUARDRAIL"
		"replay": return "REPLAY"
	return str(src).to_upper()

func _on_event(e: Dictionary) -> void:
	events_seen += 1
	var p: Dictionary = e.get("payload", {}) if typeof(e.get("payload")) == TYPE_DICTIONARY else {}
	var line := ""
	match str(e.get("type", "")):
		"MISSION_COMPLETED":
			line = "%s pulled a job (notoriety %d, heat %d)" % [str(p.get("ticker", "?")), int(p.get("notorietyPoints", 0)), int(p.get("heat", 0))]
			if str(p.get("characterId", "")) == my_character_id: _my_mission_done = true
		"NPC_OBSERVED":
			line = "%s saw: %s" % [_npc_name(str(p.get("npcId", ""))), str(p.get("summary", ""))]
		"AGENT_MESSAGE_SENT":
			line = "%s → %s  (%s)" % [_npc_name(str(p.get("senderId", ""))), _npc_name(str(p.get("recipientId", ""))), str(p.get("kind", ""))]
			_fx_message(str(p.get("senderId", "")), str(p.get("recipientId", "")), str(p.get("kind", "")))
		"DECISION_RESOLVED":
			var conf = p.get("confidence")
			var conf_txt := (" %.2f" % float(conf)) if conf != null else ""
			var ms = p.get("latencyMs")
			var ms_txt := (" %d ms" % int(ms)) if ms != null else ""
			line = "%s chose %s  [%s%s%s]" % [_npc_name(str(p.get("agentId", ""))), str(p.get("chosenAction", "?")).to_upper(), _source_label(p.get("decisionSource")), conf_txt, ms_txt]
			_fx_decision(str(p.get("agentId", "")), str(p.get("chosenAction", "?")).to_upper(), str(p.get("decisionSource", "")), conf_txt, ms_txt)
		"BULLETIN_PUBLISHED":
			line = "WANTED BULLETIN issued for %s" % str(p.get("ticker", "?"))
			_fx_bulletin(str(p.get("issuedBy", "")))
		"WANTED_STATUS_CHANGED":
			line = "%s is %s" % [str(p.get("ticker", "?")), "WANTED" if bool(p.get("wanted", false)) else "no longer wanted"]
			if str(p.get("characterId", "")) == my_character_id and my_character_id != "":
				_set_notice("YOU ARE WANTED. Police and hunters can capture you now." if bool(p.get("wanted", false)) else "You slipped the heat.", 5.0)
		"TRADE_EXECUTED":
			line = "%s %s %s @ V$%d" % [str(p.get("actorName", "?")), str(p.get("side", "")), str(p.get("ticker", "")), int(p.get("executionPrice", 0))]
		"ESCAPE_SUCCEEDED":
			line = "%s escaped the heat" % str(p.get("ticker", "?"))
		"LISTING_OPENED":
			line = "%s listed at V$%d" % [str(p.get("ticker", "?")), int(p.get("spotPrice", 0))]
		"LISTING_HALTED":
			line = "%s HALTED at V$0" % str(p.get("ticker", "?"))
		"CAPTURE_SUCCEEDED":
			line = "%s CAPTURED %s (bounty V$%d)" % [str(p.get("captorName", "?")), str(p.get("ticker", "?")), int(p.get("bounty", 0))]
			_captured_seen = true
			_captured_by = str(p.get("captorId", ""))
			var mine := str(p.get("characterId", "")) == my_character_id and my_character_id != ""
			_show_banner("%s  →  V$0" % str(p.get("ticker", "?")),
				("YOU WERE CAPTURED by %s. Press R for a new character and a new ticker." % str(p.get("captorName", "?"))) if mine
				else ("Captured by %s. Listing halted. Bounty V$%d." % [str(p.get("captorName", "?")), int(p.get("bounty", 0))]), 8.0)
		"WEAPON_FIRED":
			_shots_seen += 1
			var hit_id := str(p.get("hitId", "")) if p.get("hitId") != null else ""
			line = "%s fired a stun pulse%s" % [str(p.get("shooterName", "?")), "" if hit_id == "" else " — HIT"]
			_fx_tracer(Vector2(float(p.get("x", 0.0)), float(p.get("z", 0.0))), Vector2(float(p.get("toX", 0.0)), float(p.get("toZ", 0.0))), hit_id != "")
			var sid := str(p.get("shooterId", ""))
			if sid == my_character_id or sid == my_player_id: my_ammo = int(p.get("ammoLeft", my_ammo))
			elif game != null and typeof(AudioFX) != TYPE_NIL: AudioFX.gunshot("PISTOL")
		"STUNNED":
			_stuns_seen += 1
			var tid := str(p.get("targetId", ""))
			var secs := 3.0
			line = "%s is STUNNED for %d s (no damage)" % [str(p.get("targetName", "?")), int(secs)]
			_stun_until[tid] = _now() + secs
			if tid == my_character_id and my_character_id != "":
				_my_stun_until = _now() + secs
				_stun_pos = game.player_pos
				_set_notice("YOU ARE STUNNED — %d s" % int(secs), secs)
			print("[vice] STUNNED target=%s mine=%s" % [tid, str(tid == my_character_id)])
		"ROOM_RESET":
			line = "ROOM RESET (seeded)"
			need_teleport = true
			_feed.clear()
	if line != "":
		_feed.append(line)
		while _feed.size() > 4: _feed.pop_front()
		if record_dir != "" and _rec_n > 0: print("VICE_REC_MARK %d %s | %s" % [_rec_n, str(e.get("type", "")), line])


# ---------------------------------------------------------------- world entities
func _build_figure(kind: String, id: String) -> Node3D:
	# Uses the city's existing human rigs. Only the generic civilian / suit rigs are used here.
	var node: Node3D
	match kind:
		"POLICE": node = Human.build_model("guard_suit")
		"TRADER": node = Human.build_model("vip_suit")
		"INFORMANT": node = Human.build_model("guard_suit")
		"HUNTER": node = Human.build_model("vip_suit")
		_: node = Human.build_model("nathan")     # witnesses and outlaws: street clothes
	return node

func _glow(color: Color, energy := 3.0) -> StandardMaterial3D:
	# Lit + emissive (not unshaded) so the colour goes above 1.0 and the bloom pass picks it up.
	var m := StandardMaterial3D.new()
	m.albedo_color = Color(color.r * 0.25, color.g * 0.25, color.b * 0.25, color.a)
	m.roughness = 0.6
	m.emission_enabled = true
	m.emission = Color(color.r, color.g, color.b)
	m.emission_energy_multiplier = energy
	return m

func _orb(color: Color, radius: float) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	var sm := SphereMesh.new()
	sm.radius = radius
	sm.height = radius * 2.0
	mi.mesh = sm
	mi.material_override = _glow(color)
	return mi

func _ensure_entity(id: String, kind: String, sim: Vector2) -> Dictionary:
	if entities.has(id) and entities[id].kind == kind: return entities[id]
	if entities.has(id): _drop_entity(id)
	var node := _build_figure(kind, id)
	var body: Array = node.get_children()
	var color: Color = ROLE_COLOR.get(kind, TEXT)
	var label := Label3D.new()
	label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	label.no_depth_test = true
	label.pixel_size = 0.0046
	label.font_size = 72
	label.outline_size = 24
	label.outline_modulate = Color(0.03, 0.02, 0.08, 0.95)
	label.modulate = color
	label.position = Vector3(0, 2.62, 0)
	label.vertical_alignment = VERTICAL_ALIGNMENT_BOTTOM
	node.add_child(label)
	var ring := MeshInstance3D.new()
	var tm := TorusMesh.new()
	tm.inner_radius = 0.72
	tm.outer_radius = 0.86
	ring.mesh = tm
	var rm := _glow(color, 3.0)
	ring.material_override = rm
	ring.position = Vector3(0, 0.06, 0)
	node.add_child(ring)
	# Role beacon: a small glowing marker over the head, so roles read from across the street.
	var marker := _orb(color, 0.11)
	marker.position = Vector3(0, 2.32, 0)
	node.add_child(marker)
	var lights: Array = []
	if kind == "POLICE":
		marker.visible = false
		var red := _orb(Color("ff3355"), 0.1)
		var blue := _orb(Color("3d7bff"), 0.1)
		red.position = Vector3(-0.16, 2.32, 0)
		blue.position = Vector3(0.16, 2.32, 0)
		node.add_child(red)
		node.add_child(blue)
		lights = [red, blue]
		for pair in [[red, Color("ff2244")], [blue, Color("2f6bff")]]:
			var ol := OmniLight3D.new()
			ol.light_color = pair[1]
			ol.light_energy = 0.0
			ol.omni_range = 13.0
			ol.shadow_enabled = false
			pair[0].add_child(ol)
	game.add_child(node)
	var g := sim_to_city(sim.x, sim.y)
	var f := _free_spot(g.x, g.y)
	var pos := Vector3(f.x, _ground(f.x, f.y), f.y)
	node.position = pos
	var e := {"node": node, "label": label, "ring": ring, "marker": marker, "lights": lights, "kind": kind, "sim": sim, "pos": pos, "yaw": 0.0, "phase": randf() * TAU,
		"base": "", "extra": "", "activity": "", "pending": false, "heat": 0.0, "status": "", "moving": false,
		"body": body, "car": null, "in_car": false, "speed": 0.0, "last_sim": sim, "last_t": _now()}
	entities[id] = e
	return e

func _drop_entity(id: String) -> void:
	if not entities.has(id): return
	var n = entities[id].node
	if is_instance_valid(n): n.queue_free()
	entities.erase(id)

func _sync_entities() -> void:
	if game == null: return
	var keep := {}
	for n in state.get("npcs", []):
		var id := str(n.get("id", ""))
		var e := _ensure_entity(id, str(n.get("role", "WITNESS")), Vector2(float(n.get("x", 0.0)), float(n.get("z", 0.0))))
		e.base = "%s\n%s" % [str(n.get("name", id)), str(n.get("role", ""))]
		e.activity = str(n.get("activity", ""))
		e.pending = bool(n.get("pending", false))
		var la = n.get("lastAction")
		e.extra = ("%s [%s]" % [str(la).to_upper(), _source_label(n.get("lastSource"))]) if la != null else ""
		keep[id] = true
	for c in state.get("characters", []):
		var cid := str(c.get("id", ""))
		var st := str(c.get("status", ""))
		if cid == my_character_id or st == "CAPTURED" or st == "ARCHIVED": continue
		var ce := _ensure_entity(cid, str(c.get("role", "OUTLAW")), Vector2(float(c.get("x", 0.0)), float(c.get("z", 0.0))))
		var tk = c.get("ticker")
		var price := ""
		if tk != null:
			for l in state.get("listings", []):
				if str(l.get("ticker", "")) == str(tk): price = "  V$%d" % int(l.get("spotPrice", 0))
		if str(c.get("nickname", "")).to_lower() == "ryder":
			ce.base = "RYDER · AGENT · %s%s\nDREX-DRIVEN FUGITIVE" % [str(tk) if tk != null else "", price]
		else:
			ce.base = "%s%s\n%s" % [str(c.get("nickname", cid)), ("  " + str(tk) + price) if tk != null else "", str(c.get("role", ""))]
		ce.extra = "WANTED" if bool(c.get("wanted", false)) else ""
		ce.heat = float(c.get("heat", 0.0))
		ce.status = st
		keep[cid] = true
	for id in entities.keys():
		if not keep.has(id): _drop_entity(id)

func _update_entities(dt: float) -> void:
	for id in entities.keys():
		var e: Dictionary = entities[id]
		var node: Node3D = e.node
		if not is_instance_valid(node): continue
		var g := sim_to_city(e.sim.x, e.sim.y)
		var f := _free_spot(g.x, g.y)
		var target := Vector3(f.x, _ground(f.x, f.y), f.y)
		var d: Vector3 = target - e.pos
		var dist := Vector2(d.x, d.z).length()
		if dist > 25.0:
			e.pos = target                       # teleport (respawn / reset)
		elif dist > 0.02:
			var step := minf(dist, maxf(2.0, dist * 6.0) * dt)
			e.pos += Vector3(d.x, 0, d.z).normalized() * step
			e.pos.y = lerpf(e.pos.y, target.y, clampf(dt * 10.0, 0.0, 1.0))
		e.moving = dist > 0.12
		if e.moving:
			e.yaw = lerp_angle(e.yaw, atan2(d.x, d.z), clampf(dt * 10.0, 0.0, 1.0))
			e.phase += dt * (9.0 if dist > 1.2 else 6.0)
		elif game != null and e.kind != "OUTLAW" and e.kind != "HUNTER":
			# idle NPCs glance at the local player when close
			var pp: Vector3 = game.player_pos
			if e.pos.distance_squared_to(pp) < 100.0:
				e.yaw = lerp_angle(e.yaw, atan2(pp.x - e.pos.x, pp.z - e.pos.z), clampf(dt * 3.0, 0.0, 1.0))
		node.position = e.pos
		node.rotation.y = e.yaw
		var down: bool = _stun_until.has(id) and _now() < float(_stun_until[id])
		node.rotation.x = lerpf(node.rotation.x, -1.4 if down else 0.0, clampf(dt * 12.0, 0.0, 1.0))
		if down: node.position.y += 0.35
		_entity_car(e)
		if not down: Human.animate(node, e.phase, e.moving and not e.in_car, 0.55, 0.33)
		var txt: String = e.base
		if e.kind in ["WITNESS", "INFORMANT", "TRADER", "POLICE"]:
			if e.activity != "" and e.activity != "IDLE": txt += " · " + e.activity
			if e.pending: txt += "\n◆ DECIDING…"
		else:
			if e.status == "WANTED" or e.extra == "WANTED": txt += " · WANTED"
			if e.in_car: txt += " · DRIVING"
		if down: txt += "\n⚡ STUNNED"
		var lab: Label3D = e.label
		if lab.text != txt: lab.text = txt
		var ring: MeshInstance3D = e.ring
		var pulse := 1.0 + (0.3 + 0.3 * sin(_now() * 9.0) if e.pending else 0.0)
		ring.scale = Vector3(pulse, 1.0, pulse)
		ring.material_override.emission_energy_multiplier = 7.0 if e.pending else 3.0
		if e.kind == "OUTLAW":
			var hot_target: bool = e.status == "WANTED"
			if hot_target and not e.has("beam"):
				var beam := MeshInstance3D.new()
				var cm := CylinderMesh.new()
				cm.top_radius = 0.18
				cm.bottom_radius = 0.18
				cm.height = 36.0
				beam.mesh = cm
				var bm := _glow(Color(1.0, 0.25, 0.35, 0.55), 2.0)
				bm.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
				beam.material_override = bm
				beam.position = Vector3(0, 21.0, 0)
				node.add_child(beam)
				e["beam"] = beam
			if e.has("beam"): e.beam.visible = hot_target
		var mk: MeshInstance3D = e.marker
		mk.scale = Vector3.ONE * (1.0 + (0.6 * absf(sin(_now() * 6.0)) if e.pending else 0.0))
		var ls: Array = e.lights
		if ls.size() == 2:
			var hot: bool = e.activity == "PURSUE" or e.activity == "INVESTIGATE"
			var flip := fmod(_now() * (5.0 if e.activity == "PURSUE" else 2.0), 1.0) < 0.5
			ls[0].visible = (not hot) or flip
			ls[1].visible = (not hot) or not flip
			var power := 5.0 if e.activity == "PURSUE" else (2.2 if hot else 0.0)
			ls[0].get_child(0).light_energy = power if flip else 0.0
			ls[1].get_child(0).light_energy = power if not flip else 0.0


## Labels fade with distance, vanish beyond 45 m, and never overlap: the nearer agent wins.
func _declutter_labels() -> void:
	var cam := get_viewport().get_camera_3d()
	if cam == null: return
	var vs := get_viewport().get_visible_rect().size
	var rows: Array = []
	for id in entities.keys():
		var e: Dictionary = entities[id]
		var lab: Label3D = e.label
		var wp: Vector3 = e.pos + Vector3(0, 2.62, 0)
		var dist := cam.global_position.distance_to(wp)
		if (dist > 45.0 and not _overview) or cam.is_position_behind(wp):
			lab.visible = false
			continue
		rows.append([dist, lab, cam.unproject_position(wp)])
	rows.sort_custom(func(a, b): return a[0] < b[0])
	var placed: Array = []
	for r in rows:
		var lab2: Label3D = r[1]
		var sp: Vector2 = r[2]
		var clash := false
		for q in placed:
			if absf(sp.x - q.x) < vs.x * 0.15 and absf(sp.y - q.y) < vs.y * 0.11: clash = true
		lab2.visible = not clash
		if clash: continue
		placed.append(sp)
		var dd := float(r[0])
		var a := 1.0 if _overview else clampf((45.0 - dd) / 14.0, 0.0, 1.0)
		# Keep labels a sane size on screen: shrink when close, fade out inside 3 m, and stay out of the banner's way.
		a *= clampf((dd - 1.5) / 1.5, 0.0, 1.0)
		if hud_banner_box != null and hud_banner_box.visible: a = 0.0
		var sc := 1.0 if _overview else clampf(dd / 10.0, 0.22, 1.0)
		lab2.scale = Vector3(sc, sc, sc)
		lab2.modulate.a = a
		lab2.outline_modulate.a = 0.95 * a


func _sync_beacons() -> void:
	if game == null: return
	var keep := {}
	for m in state.get("missions", []):
		var id := str(m.get("id", ""))
		if str(m.get("status", "")) != "AVAILABLE": continue
		keep[id] = true
		if beacons.has(id): continue
		var g := sim_to_city(float(m.get("x", 0.0)), float(m.get("z", 0.0)))
		var root := Node3D.new()
		var y := _ground(g.x, g.y)
		root.position = Vector3(g.x, y, g.y)
		var beam := MeshInstance3D.new()
		var cm := CylinderMesh.new()
		cm.top_radius = 0.45
		cm.bottom_radius = 0.45
		cm.height = 40.0
		beam.mesh = cm
		var bm := _glow(Color(1.0, 0.31, 0.64, 0.5), 3.5)
		bm.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		bm.cull_mode = BaseMaterial3D.CULL_DISABLED
		beam.material_override = bm
		var pool := OmniLight3D.new()
		pool.light_color = PINK
		pool.light_energy = 3.0
		pool.omni_range = 12.0
		pool.shadow_enabled = false
		pool.position = Vector3(0, 2.0, 0)
		root.add_child(pool)
		beam.position = Vector3(0, 20.0, 0)
		root.add_child(beam)
		var disc := MeshInstance3D.new()
		var dm := CylinderMesh.new()
		dm.top_radius = INTERACT_RADIUS * SCALE.x
		dm.bottom_radius = INTERACT_RADIUS * SCALE.x
		dm.height = 0.05
		disc.mesh = dm
		var dmat := _glow(Color(1.0, 0.31, 0.64, 0.5), 2.5)
		dmat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		disc.material_override = dmat
		disc.position = Vector3(0, 0.1, 0)
		root.add_child(disc)
		var label := Label3D.new()
		label.text = "JOB\n%s" % str(m.get("label", "Mission"))
		label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
		label.no_depth_test = true
		label.pixel_size = 0.006
		label.font_size = 72
		label.outline_size = 22
		label.outline_modulate = Color(0.03, 0.02, 0.08, 0.95)
		label.modulate = PINK
		label.position = Vector3(0, 4.2, 0)
		root.add_child(label)
		game.add_child(root)
		beacons[id] = root
	for id in beacons.keys():
		if not keep.has(id):
			if is_instance_valid(beacons[id]): beacons[id].queue_free()
			beacons.erase(id)


# ---------------------------------------------------------------- look: atmosphere and district neon
func _apply_look() -> void:
	if _look_done or look == "plain" or game == null or game.env == null: return
	_look_done = true
	var env: Environment = game.env
	var dusk := time_hours >= 17.8 or (time_hours >= 0.0 and time_hours < 6.0)
	env.tonemap_mode = Environment.TONE_MAPPER_ACES
	env.tonemap_exposure = 1.2 if dusk else 1.0
	env.tonemap_white = 6.0
	env.glow_enabled = true
	env.glow_intensity = 0.55
	env.glow_strength = 1.0
	env.glow_bloom = 0.04
	env.glow_blend_mode = Environment.GLOW_BLEND_MODE_ADDITIVE
	env.glow_hdr_threshold = 1.15
	env.glow_hdr_scale = 2.0
	for i in 7: env.set_glow_level(i, 1.0 if (i >= 1 and i <= 3) else 0.0)
	env.adjustment_enabled = true
	env.adjustment_contrast = 1.12
	env.adjustment_saturation = 1.32
	env.volumetric_fog_enabled = true
	env.volumetric_fog_density = 0.0042 if dusk else 0.0025
	env.volumetric_fog_albedo = Color(0.78, 0.60, 0.98)
	env.volumetric_fog_emission = Color(0.16, 0.05, 0.22) if dusk else Color(0.10, 0.05, 0.02)
	env.volumetric_fog_emission_energy = 0.3
	env.volumetric_fog_length = 120.0
	env.volumetric_fog_anisotropy = 0.5
	env.volumetric_fog_sky_affect = 0.0
	# Wet-look asphalt so neon and lamps streak across the road.
	var road = game.world.get("_road_mat")
	if road != null:
		road.roughness = 0.28
		road.metallic = 0.3
	# Two soft coloured fills (no shadows): pink from one side, aqua from the other.
	_fill_a = DirectionalLight3D.new()
	_fill_a.light_color = Color(1.0, 0.36, 0.72)
	_fill_a.light_energy = 0.55 if dusk else 0.16
	_fill_a.shadow_enabled = false
	_fill_a.rotation_degrees = Vector3(-26, 115, 0)
	game.add_child(_fill_a)
	_fill_b = DirectionalLight3D.new()
	_fill_b.light_color = Color(0.25, 0.88, 1.0)
	_fill_b.light_energy = 0.5 if dusk else 0.14
	_fill_b.shadow_enabled = false
	_fill_b.rotation_degrees = Vector3(-22, -65, 0)
	game.add_child(_fill_b)
	_build_neon()

func _neon_box(parent: Node3D, pos: Vector3, size: Vector3, mat: Material) -> void:
	var mi := MeshInstance3D.new()
	var bm := BoxMesh.new()
	bm.size = size
	mi.mesh = bm
	mi.material_override = mat
	mi.position = pos
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	parent.add_child(mi)

## Our own neon on the mapped district: corner tubes, roofline and street-level bands, and a few
## original text signs. Procedural boxes only; nothing here imitates a real sign or logo.
func _build_neon() -> void:
	var root := Node3D.new()
	root.name = "ViceNeon"
	game.add_child(root)
	var cols := [AQUA, PINK, Color("a26bff")]
	var mats := [_glow(cols[0], 2.3), _glow(cols[1], 2.3), _glow(cols[2], 2.3)]
	var signs := ["VICE EXCHANGE", "CORAL BAY", "V$ LIVE", "THE SWARM DECIDES", "EVERY LIFE IS A TICKER", "HEAT RISING"]
	var lo := sim_to_city(-16.0, -16.0)
	var hi := sim_to_city(136.0, 96.0)
	var i := 0
	for b in game.world.buildings:
		if typeof(b) != TYPE_DICTIONARY: continue
		if absf(float(b.get("yaw", 0.0))) > 0.001: continue
		var bx := float(b.get("x", 0.0))
		var bz := float(b.get("z", 0.0))
		if bx < lo.x or bx > hi.x or bz < lo.y or bz > hi.y: continue
		var w := float(b.get("w", 0.0))
		var d := float(b.get("d", 0.0))
		var h := float(b.get("h", 0.0))
		if w < 6.0 or d < 6.0 or h < 7.0 or h > 120.0: continue
		var m: Material = mats[i % 3]
		var col: Color = cols[i % 3]
		for sx in [-1.0, 1.0]:
			for sz in [-1.0, 1.0]:
				_neon_box(root, Vector3(bx + sx * (w / 2.0 + 0.12), (h + 4.6) / 2.0, bz + sz * (d / 2.0 + 0.12)), Vector3(0.16, h - 4.6, 0.16), m)
		for y in [h + 0.15, 4.6]:
			_neon_box(root, Vector3(bx, y, bz + d / 2.0 + 0.12), Vector3(w + 0.4, 0.15, 0.15), m)
			_neon_box(root, Vector3(bx, y, bz - d / 2.0 - 0.12), Vector3(w + 0.4, 0.15, 0.15), m)
			_neon_box(root, Vector3(bx + w / 2.0 + 0.12, y, bz), Vector3(0.15, 0.15, d + 0.4), m)
			_neon_box(root, Vector3(bx - w / 2.0 - 0.12, y, bz), Vector3(0.15, 0.15, d + 0.4), m)
		# colour spill onto the pavement on two faces
		for sz2 in [-1.0, 1.0]:
			var ol := OmniLight3D.new()
			ol.light_color = col
			ol.light_energy = 1.1
			ol.omni_range = 14.0
			ol.shadow_enabled = false
			ol.position = Vector3(bx, 4.2, bz + sz2 * (d / 2.0 + 2.2))
			root.add_child(ol)
		# a text sign on the south face of every other building
		if i % 2 == 0 and w >= 9.0:
			var sign := Label3D.new()
			sign.text = signs[(i / 2) % signs.size()]
			sign.font_size = 120
			sign.pixel_size = minf(0.011, (w - 1.0) / (float(sign.text.length()) * 70.0))
			sign.modulate = cols[(i + 1) % 3]
			sign.outline_size = 10
			sign.outline_modulate = Color(0.02, 0.01, 0.06, 0.9)
			sign.position = Vector3(bx, minf(h - 2.0, 8.2), bz + d / 2.0 + 0.2)
			root.add_child(sign)
			_neon_box(root, Vector3(bx, minf(h - 2.0, 8.2) - 1.15, bz + d / 2.0 + 0.16), Vector3(w * 0.7, 0.14, 0.14), mats[(i + 1) % 3])
		i += 1
	print("[vice] neon trim on %d buildings" % i)


# ---------------------------------------------------------------- swarm made visible
func _head_pos(id: String) -> Vector3:
	if entities.has(id): return entities[id].pos + Vector3(0, 2.15, 0)
	if (id == my_player_id or id == my_character_id) and game != null: return game.player_pos + Vector3(0, 2.15, 0)
	return Vector3.INF

## A light travels sender -> recipient along an arc while the message is in transit.
func _fx_message(from_id: String, to_id: String, kind: String) -> void:
	if game == null or not _started_game: return
	var a := _head_pos(from_id)
	var b := _head_pos(to_id)
	if a == Vector3.INF or b == Vector3.INF: return
	var color: Color = MSG_COLOR.get(kind, AQUA)
	var root := Node3D.new()
	game.add_child(root)
	var mat := _glow(color, 2.6)      # bright enough to bloom, low enough to keep the kind's colour
	var dots: Array = []
	var n := clampi(int(a.distance_to(b) / 0.8), 12, 80)
	for i in n:
		var dot := MeshInstance3D.new()
		var sm := SphereMesh.new()
		sm.radius = 0.15
		sm.height = 0.3
		sm.radial_segments = 8
		sm.rings = 4
		dot.mesh = sm
		dot.material_override = mat
		dot.visible = false
		root.add_child(dot)
		dots.append(dot)
	var head := MeshInstance3D.new()
	var hm := SphereMesh.new()
	hm.radius = 0.42
	hm.height = 0.84
	head.mesh = hm
	head.material_override = mat
	root.add_child(head)
	var light := OmniLight3D.new()
	light.light_color = color
	light.light_energy = 5.0
	light.omni_range = 12.0
	light.shadow_enabled = false
	head.add_child(light)
	_fx.append({"type": "arc", "root": root, "dots": dots, "head": head, "from": from_id, "to": to_id, "a": a, "b": b, "t": 0.0, "travel": 1.25, "life": 3.2})
	_last_arc = {"a": a, "b": b}
	if shots_dir != "" or record_dir != "": print("VICE_FX arc %s %s -> %s  a=%s b=%s dots=%d" % [kind, from_id, to_id, str(a), str(b), n])

## A wanted bulletin goes out to everyone: a gold ring expands from the officer who issued it.
func _fx_bulletin(issuer_id: String) -> void:
	if game == null or not _started_game: return
	var a := _head_pos(issuer_id)
	if a == Vector3.INF: return
	var ring := MeshInstance3D.new()
	var tm := TorusMesh.new()
	tm.inner_radius = 0.996
	tm.outer_radius = 1.0
	ring.mesh = tm
	var mat := _glow(MSG_COLOR["BULLETIN"], 5.0)
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	ring.material_override = mat
	ring.position = Vector3(a.x, a.y - 1.9, a.z)
	game.add_child(ring)
	_fx.append({"type": "ring", "root": ring, "mat": mat, "t": 0.0, "life": 1.8})

## The decision floats over the agent's head for a few seconds, with who really decided it.
func _fx_decision(agent_id: String, action: String, source: String, conf_txt: String, ms_txt: String) -> void:
	if game == null or not _started_game or not entities.has(agent_id): return
	var e: Dictionary = entities[agent_id]
	if e.has("popup") and is_instance_valid(e.popup): e.popup.queue_free()
	var lab := Label3D.new()
	lab.text = "%s\n%s%s%s" % [action, _source_label(source), conf_txt, ms_txt]
	lab.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	lab.no_depth_test = true
	lab.pixel_size = 0.0052
	lab.font_size = 84
	lab.outline_size = 26
	lab.outline_modulate = Color(0.03, 0.02, 0.08, 0.95)
	lab.modulate = SOURCE_COLOR.get(source, TEXT)
	lab.position = Vector3(0, 3.9, 0)
	e.node.add_child(lab)
	e["popup"] = lab
	_fx.append({"type": "popup", "root": lab, "t": 0.0, "life": 7.0})

func _update_fx(dt: float) -> void:
	var keep: Array = []
	for f in _fx:
		f.t += dt
		var root = f.root
		if not is_instance_valid(root): continue
		if f.t >= f.life:
			root.queue_free()
			continue
		keep.append(f)
		match str(f.type):
			"arc":
				var a := _head_pos(str(f.from))
				var b := _head_pos(str(f.to))
				if a != Vector3.INF: f.a = a
				if b != Vector3.INF: f.b = b
				var pa: Vector3 = f.a
				var pb: Vector3 = f.b
				var lift := clampf(pa.distance_to(pb) * 0.28, 3.0, 20.0)
				var prog := clampf(f.t / f.travel, 0.0, 1.0)
				var fade := clampf((f.life - f.t) / 0.9, 0.0, 1.0)
				var dots: Array = f.dots
				for i in dots.size():
					var u := float(i + 1) / float(dots.size() + 1)
					var dot: MeshInstance3D = dots[i]
					dot.visible = u <= prog
					dot.position = pa.lerp(pb, u) + Vector3.UP * sin(u * PI) * lift
					dot.scale = Vector3.ONE * fade
				var head: MeshInstance3D = f.head
				head.position = pa.lerp(pb, prog) + Vector3.UP * sin(prog * PI) * lift
				head.scale = Vector3.ONE * (fade if prog >= 1.0 else 1.0)
			"ring":
				var k: float = f.t / f.life
				var r := lerpf(3.0, 52.0, k)
				root.scale = Vector3(r, r, r)
				f.mat.albedo_color.a = 1.0 - k
				f.mat.emission_energy_multiplier = 5.0 * (1.0 - k)
			"popup":
				var lab: Label3D = root
				lab.position.y = 3.9 + f.t * 0.12
				var al := clampf((f.life - f.t) / 1.2, 0.0, 1.0)
				var pcam := get_viewport().get_camera_3d()
				if pcam != null:
					var pd := pcam.global_position.distance_to(lab.global_position)
					var ps := clampf(pd / 11.0, 0.2, 1.0)
					lab.scale = Vector3(ps, ps, ps)
					al *= clampf((pd - 1.5) / 1.5, 0.0, 1.0) * clampf((50.0 - pd) / 12.0, 0.0, 1.0)
				if hud_banner_box != null and hud_banner_box.visible: al = 0.0
				lab.modulate.a = al
				lab.outline_modulate.a = 0.95 * al
	_fx = keep


# ---------------------------------------------------------------- local player
func _my_character() -> Dictionary:
	for c in state.get("characters", []):
		if str(c.get("id", "")) == my_character_id: return c
	return {}

func _my_listing() -> Dictionary:
	var c := _my_character()
	if c.is_empty() or c.get("ticker") == null: return {}
	for l in state.get("listings", []):
		if str(l.get("ticker", "")) == str(c.get("ticker")): return l
	return {}

func _teleport_to_server_position() -> void:
	var c := _my_character()
	if c.is_empty():
		need_teleport = false
		return
	var g := sim_to_city(float(c.get("x", 60.0)), float(c.get("z", 40.0)))
	var f := _free_spot(g.x, g.y, 0.8)
	game.player_pos = Vector3(f.x, _ground(f.x, f.y), f.y)
	if game.player_node != null: game.player_node.position = game.player_pos
	_last_pose = Vector2(-999, -999)
	need_teleport = false
	print("[vice] local player placed at city (%.1f, %.1f) = sim (%.1f, %.1f)" % [f.x, f.y, float(c.get("x", 0.0)), float(c.get("z", 0.0))])

func _send_pose(dt: float) -> void:
	if my_character_id == "": return
	var c := _my_character()
	var st := str(c.get("status", ""))
	if st != "ACTIVE" and st != "WANTED": return
	var fast: bool = _udp != null and _udp.active
	_pose_t -= dt
	if _pose_t > 0.0: return
	_pose_t = _udp.POSE_INTERVAL if fast else POSE_INTERVAL
	var s := my_sim_pos()
	var clamped := Vector2(clampf(s.x, SIM_MIN.x, SIM_MAX.x), clampf(s.y, SIM_MIN.y, SIM_MAX.y))
	outside_sector = clamped.distance_to(s) > 0.75
	if clamped.distance_to(_last_pose) < 0.03 and _now() - _last_pose_sent_at < 1.0: return
	_last_pose = clamped
	_last_pose_sent_at = _now()
	if fast:
		# Same pose, same server checks, sent as a datagram at up to 30 Hz (heading in radians; flags: 1 in a car).
		var yaw := 0.0
		if game != null and game.player_node != null: yaw = game.player_node.rotation.y
		_udp.send_pose(clamped.x, clamped.y, yaw, 0)
		return
	_send({"type": "player.pose", "x": snappedf(clamped.x, 0.01), "z": snappedf(clamped.y, 0.01)}, "pose")

func _update_prompts() -> void:
	_prompt_action = ""
	_prompt_target = ""
	var c := _my_character()
	if c.is_empty(): return
	var st := str(c.get("status", ""))
	if st == "CAPTURED":
		_prompt_action = "respawn"
		return
	var s := my_sim_pos()
	if my_role == "OUTLAW":
		for m in state.get("missions", []):
			if str(m.get("status", "")) != "AVAILABLE": continue
			if s.distance_to(Vector2(float(m.get("x", 0.0)), float(m.get("z", 0.0)))) <= INTERACT_RADIUS:
				_prompt_action = "mission"
				_prompt_target = str(m.get("id", ""))
				return
	elif my_role == "HUNTER":
		for o in state.get("characters", []):
			if str(o.get("id", "")) == my_character_id or not bool(o.get("wanted", false)): continue
			if str(o.get("status", "")) != "WANTED": continue
			var id := str(o.get("id", ""))
			var os: Vector2 = entities[id].sim if entities.has(id) else Vector2(float(o.get("x", 0.0)), float(o.get("z", 0.0)))
			if s.distance_to(os) <= INTERACT_RADIUS:
				_prompt_action = "capture"
				_prompt_target = id
				return

## Keys are polled, not taken from _input: the city's own input handler runs first and marks
## its interact key as handled, so an event-based hook here would never see E.
func _poll_keys() -> void:
	if not joined: return
	for k in [KEY_E, KEY_R, KEY_BRACKETLEFT, KEY_BRACKETRIGHT]:
		var down := Input.is_physical_key_pressed(k)
		if down and not bool(_key_down.get(k, false)): _on_key(k)
		_key_down[k] = down

func _on_key(key: int) -> void:
	if key == KEY_E:
		if _prompt_action == "mission":
			_send({"type": "mission.interact", "missionId": _prompt_target, "commandId": _cid("gmis")}, "mission")
		elif _prompt_action == "capture":
			_send({"type": "capture.attempt", "targetCharacterId": _prompt_target, "commandId": _cid("gcap")}, "capture")
	elif key == KEY_R and _prompt_action == "respawn":
		_send({"type": "character.respawn", "commandId": _cid("grsp")}, "respawn")
	elif key == KEY_BRACKETRIGHT or key == KEY_BRACKETLEFT:
		var tk := _focus_ticker()
		if tk != "":
			_send({"type": "market.buy" if key == KEY_BRACKETRIGHT else "market.sell", "ticker": tk, "qty": 1, "commandId": _cid("gtrd")}, "trade")

func _focus_ticker() -> String:
	var l := _my_listing()
	if not l.is_empty(): return str(l.get("ticker", ""))
	for o in state.get("listings", []):
		if str(o.get("status", "")) == "ACTIVE": return str(o.get("ticker", ""))
	return ""

func _nearest_mission_dist() -> float:
	var s := my_sim_pos()
	var best := -1.0
	for m in state.get("missions", []):
		if str(m.get("status", "")) != "AVAILABLE": continue
		var d := s.distance_to(Vector2(float(m.get("x", 0.0)), float(m.get("z", 0.0))))
		if best < 0.0 or d < best: best = d
	return best


# ---------------------------------------------------------------- HUD
func _label(size: int, color: Color) -> Label:
	var l := Label.new()
	l.add_theme_font_size_override("font_size", size)
	l.add_theme_color_override("font_color", color)
	l.add_theme_color_override("font_outline_color", Color(0.02, 0.01, 0.05, 0.9))
	l.add_theme_constant_override("outline_size", 6)
	l.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	l.mouse_filter = Control.MOUSE_FILTER_IGNORE
	return l

func _panel(border: Color) -> PanelContainer:
	var p := PanelContainer.new()
	var sb := StyleBoxFlat.new()
	sb.bg_color = Color(0.05, 0.03, 0.10, 0.86)
	sb.border_color = border
	sb.set_border_width_all(2)
	sb.set_corner_radius_all(10)
	sb.content_margin_left = 22
	sb.content_margin_right = 22
	sb.content_margin_top = 10
	sb.content_margin_bottom = 10
	p.add_theme_stylebox_override("panel", sb)
	p.mouse_filter = Control.MOUSE_FILTER_IGNORE
	return p

func _build_hud() -> void:
	hud_layer = CanvasLayer.new()
	hud_layer.layer = 60
	add_child(hud_layer)
	var root := Control.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	hud_layer.add_child(root)

	var top := CenterContainer.new()
	top.set_anchors_preset(Control.PRESET_TOP_WIDE)
	top.offset_top = 64
	top.offset_bottom = 230
	top.mouse_filter = Control.MOUSE_FILTER_IGNORE
	root.add_child(top)
	var strip := _panel(PINK)
	top.add_child(strip)
	var vb := VBoxContainer.new()
	vb.add_theme_constant_override("separation", 2)
	vb.mouse_filter = Control.MOUSE_FILTER_IGNORE
	strip.add_child(vb)
	hud_title = _label(19, AQUA)
	hud_main = _label(38, TEXT)
	hud_stats = _label(22, DIM)
	hud_feed = _label(18, Color("d7c8ff"))
	vb.add_child(hud_title)
	vb.add_child(hud_main)
	# Our own heat meter: one bar plus "WANTED LEVEL n/5" (the only wanted indicator on screen).
	var heat_wrap := CenterContainer.new()
	heat_wrap.mouse_filter = Control.MOUSE_FILTER_IGNORE
	vb.add_child(heat_wrap)
	hud_heat_row = HBoxContainer.new()
	hud_heat_row.add_theme_constant_override("separation", 14)
	hud_heat_row.mouse_filter = Control.MOUSE_FILTER_IGNORE
	heat_wrap.add_child(hud_heat_row)
	var hl := _label(20, DIM)
	hl.text = "HEAT"
	hud_heat_row.add_child(hl)
	var track := ColorRect.new()
	track.color = Color(1, 1, 1, 0.10)
	track.custom_minimum_size = Vector2(300, 16)
	track.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	track.mouse_filter = Control.MOUSE_FILTER_IGNORE
	hud_heat_row.add_child(track)
	hud_heat_fill = ColorRect.new()
	hud_heat_fill.color = AQUA
	hud_heat_fill.size = Vector2(0, 16)
	hud_heat_fill.mouse_filter = Control.MOUSE_FILTER_IGNORE
	track.add_child(hud_heat_fill)
	for i in range(1, 5):                      # level ticks every 20 heat
		var tick := ColorRect.new()
		tick.color = Color(0.05, 0.03, 0.10, 0.9)
		tick.position = Vector2(300.0 * i / 5.0 - 1.0, 0)
		tick.size = Vector2(2, 16)
		tick.mouse_filter = Control.MOUSE_FILTER_IGNORE
		track.add_child(tick)
	hud_heat_text = _label(20, DIM)
	hud_heat_row.add_child(hud_heat_text)
	vb.add_child(hud_stats)
	vb.add_child(hud_feed)

	var bottom := CenterContainer.new()
	bottom.set_anchors_preset(Control.PRESET_BOTTOM_WIDE)
	bottom.offset_top = -250
	bottom.offset_bottom = -190
	bottom.mouse_filter = Control.MOUSE_FILTER_IGNORE
	root.add_child(bottom)
	hud_prompt_panel = _panel(AQUA)
	bottom.add_child(hud_prompt_panel)
	hud_prompt = _label(24, TEXT)
	hud_prompt_panel.add_child(hud_prompt)
	hud_prompt_panel.visible = false

	var hint := _label(17, DIM)
	hint.set_anchors_preset(Control.PRESET_BOTTOM_LEFT)
	hint.offset_left = 22
	hint.offset_top = -44
	hint.offset_bottom = -16
	hint.offset_right = 900
	hint.horizontal_alignment = HORIZONTAL_ALIGNMENT_LEFT
	hint.text = "WASD move   ·   E job / capture   ·   R respawn   ·   ] buy 1   [ sell 1   ·   F enter car"
	root.add_child(hint)

	var mid := CenterContainer.new()
	mid.set_anchors_preset(Control.PRESET_FULL_RECT)
	mid.mouse_filter = Control.MOUSE_FILTER_IGNORE
	root.add_child(mid)
	var bp := _panel(DANGER)
	mid.add_child(bp)
	var bvb := VBoxContainer.new()
	bvb.mouse_filter = Control.MOUSE_FILTER_IGNORE
	bp.add_child(bvb)
	hud_banner = _label(84, DANGER)
	hud_banner_sub = _label(26, TEXT)
	bvb.add_child(hud_banner)
	bvb.add_child(hud_banner_sub)
	hud_banner_box = bp
	bp.visible = false

func _set_notice(text: String, secs: float) -> void:
	_notice = text
	_notice_until = _now() + secs

func _show_banner(big: String, sub: String, secs: float) -> void:
	hud_banner.text = big
	hud_banner_sub.text = sub
	hud_banner_box.visible = true
	_banner_until = _now() + secs

func _stars(level: int) -> String:
	var s := ""
	for i in 5: s += "★" if i < level else "☆"
	return s

func _update_hud() -> void:
	if hud_layer == null: return
	if hud_banner_box.visible and _now() > _banner_until: hud_banner_box.visible = false
	if not ws_open:
		hud_title.text = "VICE EXCHANGE"
		hud_main.text = "CONNECTING TO THE EXCHANGE…"
		hud_stats.text = url
		hud_feed.text = ""
		hud_prompt_panel.visible = false
		return
	if not joined or not have_state:
		hud_title.text = "VICE EXCHANGE"
		hud_main.text = "JOINING ROOM %s…" % room_code
		hud_stats.text = ""
		hud_feed.text = ""
		return
	hud_title.text = "VICE EXCHANGE   ·   ROOM %s   ·   %s   ·   %s   ·   V$ ARE FICTIONAL GAME CREDITS" % [str(state.get("roomCode", room_code)), str(state.get("badge", "")), _udp.hud_text() if _udp != null else "NET: WS 10 Hz"]
	var c := _my_character()
	var w = state.get("wallet")
	var wallet_txt := ("WALLET V$%d" % int(w.get("balanceCredits", 0))) if typeof(w) == TYPE_DICTIONARY else ""
	if c.is_empty():
		hud_heat_row.visible = false
		hud_main.text = "SPECTATING"
		hud_stats.text = "%d swarm agents live" % state.get("npcs", []).size()
	elif my_role == "OUTLAW":
		var l := _my_listing()
		var st := str(c.get("status", ""))
		if st == "CAPTURED" or l.is_empty() or str(l.get("status", "")) != "ACTIVE":
			hud_main.text = "%s   V$0   HALTED" % str(c.get("ticker", "?"))
			hud_main.add_theme_color_override("font_color", DANGER)
		else:
			hud_main.text = "%s   V$%d %s    BOUNTY ON YOU V$%d" % [str(l.get("ticker", "?")), int(l.get("spotPrice", 0)), _trend, int(l.get("currentBounty", 0))]
			hud_main.add_theme_color_override("font_color", GOOD if _trend == "▲" else (DANGER if _trend == "▼" else TEXT))
		var md := _nearest_mission_dist()
		var heat := clampf(float(c.get("heat", 0)), 0.0, 100.0)
		var lvl := int(c.get("wantedLevel", 0))
		var is_wanted := bool(c.get("wanted", false))
		hud_heat_row.visible = true
		hud_heat_fill.size = Vector2(300.0 * heat / 100.0, 16)
		hud_heat_fill.color = AQUA.lerp(DANGER, clampf(heat / 60.0, 0.0, 1.0))
		hud_heat_text.text = "%d   WANTED LEVEL %d/5" % [int(heat), lvl] if is_wanted else "%d   NOT WANTED" % int(heat)
		hud_heat_text.add_theme_color_override("font_color", DANGER if is_wanted else DIM)
		hud_stats.text = "%s   %s%s%s" % [wallet_txt, ("JOB %d m" % int(md)) if md >= 0.0 else "NO JOB YET", _gun_txt(c), "   OUT OF THE DISTRICT" if outside_sector else ""]
	else:
		var tgt := ""
		for o in state.get("characters", []):
			if str(o.get("role", "")) != "OUTLAW" or str(o.get("status", "")) in ["CAPTURED", "ARCHIVED"]: continue
			var bounty := 0
			for l2 in state.get("listings", []):
				if str(l2.get("ticker", "")) == str(o.get("ticker", "")): bounty = int(l2.get("currentBounty", 0))
			var os: Vector2 = entities[str(o.get("id", ""))].sim if entities.has(str(o.get("id", ""))) else Vector2.ZERO
			tgt += "%s%s %s  bounty V$%d  %d m" % ["   |   " if tgt != "" else "", str(o.get("ticker", "?")), "WANTED" if bool(o.get("wanted", false)) else "not wanted", bounty, int(my_sim_pos().distance_to(os))]
		hud_heat_row.visible = false
		hud_main.text = "HUNTER   %s" % (tgt if tgt != "" else "no outlaws in the city")
		hud_main.add_theme_color_override("font_color", TEXT)
		hud_stats.text = "%s%s%s" % [wallet_txt, _gun_txt(c), "   OUT OF THE DISTRICT" if outside_sector else ""]
	hud_feed.text = "\n".join(_feed)
	var prompt := ""
	match _prompt_action:
		"mission": prompt = "[E]  GRAB THE PACKAGE"
		"capture": prompt = "[E]  CAPTURE"
		"respawn": prompt = "[R]  RESPAWN AS A NEW CHARACTER (NEW TICKER)"
	if _now() < _notice_until: prompt = _notice
	hud_prompt.text = prompt
	hud_prompt_panel.visible = prompt != ""


# ---------------------------------------------------------------- verification modes
## Recording only: a street-side camera that holds the outlaw, the witness and the approaching
## police in one frame while the chain plays out, then hands back to the chase camera on respawn.
func _direct_camera(mec: Dictionary) -> void:
	var dt := get_process_delta_time()
	if _respawn_done:
		if _cine_cam != null:
			_cine_cam.queue_free()
			_cine_cam = null
			if game.camera != null: game.camera.current = true
			game.cam_pitch = 0.16
		game.cam_yaw += 0.25 * dt
		return
	var pp: Vector3 = game.player_pos
	if _cine_cam == null:
		_cine_cam = Camera3D.new()
		_cine_cam.far = 3000.0
		_cine_cam.fov = 58.0
		game.add_child(_cine_cam)
		_cine_anchor = pp
		_cine_t = 0.0
		# pick the clearest street-side spot around the outlaw (prefers the open road to the north-east)
		var best := Vector3(pp.x + 12.0, 3.3, pp.z - 5.0)
		for cand in [Vector2(12.0, -5.0), Vector2(11.0, -8.0), Vector2(-12.0, -5.0), Vector2(8.0, -11.0), Vector2(-8.0, -11.0), Vector2(13.0, 2.0)]:
			var cx: float = pp.x + cand.x
			var cz: float = pp.z + cand.y
			var clear: bool = not game.world.collides_at(cx, cz, 1.5)
			for u in [0.25, 0.5, 0.75]:
				if game.world.collides_at(lerpf(cx, pp.x, u), lerpf(cz, pp.z, u), 0.6): clear = false
			if clear:
				best = Vector3(cx, 3.3, cz)
				break
		_cine_cam.position = best
		_cine_look = Vector3(pp.x - 2.0, 1.5, pp.z - 0.5)
		_cine_cam.current = true
	_cine_t += dt
	var chaser := Vector3.INF
	for id in entities.keys():
		if entities[id].kind == "POLICE" and entities[id].activity == "PURSUE": chaser = entities[id].pos
	if chaser != Vector3.INF and not _capture_shot:
		# two-shot: stand off to the side of the line between the officer and the outlaw
		var mid: Vector3 = (pp + chaser) * 0.5
		var dir := Vector3(chaser.x - pp.x, 0, chaser.z - pp.z)
		var span := maxf(dir.length(), 0.1)
		dir = dir / span
		var off := maxf(9.0, span * 0.75 + 5.0)
		var best_pos: Vector3 = _cine_cam.position
		var best_d := 1e9
		for sgn in [-1.0, 1.0]:
			var cpos := Vector3(mid.x - dir.z * off * sgn, 3.6, mid.z + dir.x * off * sgn)
			if game.world.collides_at(cpos.x, cpos.z, 1.5): continue
			var dd: float = cpos.distance_to(_cine_cam.position)
			if dd < best_d:
				best_d = dd
				best_pos = cpos
		_cine_cam.position = _cine_cam.position.lerp(best_pos, clampf(dt * 2.2, 0.0, 1.0))
		_cine_look = _cine_look.lerp(Vector3(mid.x, 1.5, mid.z), clampf(dt * 3.0, 0.0, 1.0))
		_cine_cam.look_at(_cine_look)
		return
	_cine_look = _cine_look.lerp(Vector3(_cine_anchor.x - 2.0, 1.5, _cine_anchor.z - 0.5), clampf(dt * 2.0, 0.0, 1.0))
	# slow push-in toward the outlaw
	var home: Vector3 = _cine_cam.position
	var want := Vector3(lerpf(home.x, _cine_anchor.x, 0.012 * dt * 10.0), 3.3 + 0.5 * sin(_cine_t * 0.2), lerpf(home.z, _cine_anchor.z, 0.006 * dt * 10.0))
	if Vector2(want.x - _cine_anchor.x, want.z - _cine_anchor.z).length() > 7.5 and not _capture_shot: _cine_cam.position = want
	_cine_cam.look_at(_cine_look)

## Real-time capture for the fallback clip: frames are stamped with their true duration, so the
## video plays at the speed the scene actually ran, whatever the capture rate was.
func _record_frame() -> void:
	if record_dir == "" or not _started_game or not joined: return
	var now := _now()
	if _rec_t0 < 0.0:
		_rec_t0 = now
		_rec_last = now - 1.0
	if now - _rec_last < 1.0 / record_fps: return
	var img := get_viewport().get_texture().get_image()
	if img == null: return
	var fname := "f_%05d.jpg" % _rec_n
	img.save_jpg(record_dir.path_join(fname), 0.88)
	if _rec_n > 0: _rec_list.append("duration %.4f" % (now - _rec_last))
	_rec_list.append("file '%s'" % fname)
	_rec_last = now
	_rec_n += 1

func _finish_recording() -> void:
	if record_dir == "" or _rec_n == 0: return
	_rec_list.append("duration 0.0417")
	var f := FileAccess.open(record_dir.path_join("frames.txt"), FileAccess.WRITE)
	if f != null:
		f.store_string("\n".join(_rec_list) + "\n")
		f.close()
	print("VICE_RECORDING frames=%d seconds=%.1f dir=%s" % [_rec_n, _rec_last - _rec_t0, record_dir])

func _press(keycode: int) -> void:
	for pressed in [true, false]:
		var ev := InputEventKey.new()
		ev.physical_keycode = keycode
		ev.keycode = keycode
		ev.pressed = pressed
		Input.parse_input_event(ev)
		if pressed: await get_tree().create_timer(0.12).timeout

func _shot_later(name: String, delay: float) -> void:
	if shots_dir == "": return
	await get_tree().create_timer(delay).timeout
	await _shot(name)

func _fail(reason: String) -> void:
	_finish_recording()
	print("VICE_BRIDGE_FAIL ", reason)
	get_tree().quit(1)

func _run_check() -> void:
	var t := _now() - _check_started_at
	if _check_stage == "":
		if joined and have_state and ticks_seen >= 10:
			var npcs: Array = state.get("npcs", [])
			print("VICE_BRIDGE_JOINED npcs=%d ticks=%d entities=%d character=%s" % [npcs.size(), ticks_seen, entities.size(), my_character_id])
			for m in state.get("missions", []):
				var g := sim_to_city(float(m.get("x", 0.0)), float(m.get("z", 0.0)))
				print("VICE_MISSION %s sim=(%.1f,%.1f) city=(%.1f,%.1f) blocked_in_city=%s" % [str(m.get("id", "")), float(m.get("x", 0.0)), float(m.get("z", 0.0)), g.x, g.y, str(game.world.collides_at(g.x, g.y, 0.5))])
			for id in entities.keys():
				var e: Dictionary = entities[id]
				var gg := sim_to_city(e.sim.x, e.sim.y)
				print("VICE_ENTITY %s kind=%s sim=(%.1f,%.1f) city=(%.1f,%.1f) shown=(%.1f,%.1f)" % [id, e.kind, e.sim.x, e.sim.y, gg.x, gg.y, e.pos.x, e.pos.z])
			_check_stage = ("intro" if record_dir != "" else "trade") if my_role == "OUTLAW" else ("hunt" if hunt else "linger")
			_check_started_at = _now()
		elif t > 20.0:
			_fail("no join/state/ticks within 20 s (open=%s joined=%s state=%s ticks=%d)" % [str(ws_open), str(joined), str(have_state), ticks_seen])
	elif _check_stage == "intro":
		# Recording only: an establishing flyover from above the district down to the player.
		if _intro_cam == null:
			_intro_cam = Camera3D.new()
			_intro_cam.far = 3000.0
			_intro_cam.fov = 60.0
			game.add_child(_intro_cam)
			_intro_cam.current = true
			_overview = true
		var k := clampf(t / 6.0, 0.0, 1.0)
		var ease := k * k * (3.0 - 2.0 * k)
		var pp: Vector3 = game.player_pos
		var c := sim_to_city(52.0, 44.0)
		var from := Vector3(c.x - 46.0, 58.0, c.y + 74.0)
		var to := Vector3(pp.x + 3.0, pp.y + 7.5, pp.z + 13.0)
		_intro_cam.position = from.lerp(to, ease)
		_intro_cam.look_at(Vector3(c.x, 6.0, c.y).lerp(Vector3(pp.x, pp.y + 1.4, pp.z), ease))
		if k >= 1.0:
			_intro_cam.queue_free()
			_intro_cam = null
			_overview = false
			if game.camera != null: game.camera.current = true
			game.cam_yaw = PI
			game.cam_pitch = 0.16
			_check_stage = "trade"
			_check_started_at = _now()
	elif _check_stage == "trade":
		# Buy one share of my own ticker with the real key, and confirm the wallet moved.
		if not _trade_pressed:
			_trade_pressed = true
			var w0 = state.get("wallet")
			_trade_balance0 = int(w0.get("balanceCredits", 0)) if typeof(w0) == TYPE_DICTIONARY else 0
			_press(KEY_BRACKETRIGHT)
		var w1 = state.get("wallet")
		var bal := int(w1.get("balanceCredits", 0)) if typeof(w1) == TYPE_DICTIONARY else 0
		if bal < _trade_balance0:
			var held := 0
			for h in w1.get("holdings", []): held += int(h.get("shares", 0))
			print("VICE_BRIDGE_TRADE_OK balance %d -> %d shares_held=%d" % [_trade_balance0, bal, held])
			_check_stage = "walk"
			_check_started_at = _now()
		elif t > 5.0:
			_fail("buy order did not change the wallet (notice=%s)" % _notice)
	elif _check_stage == "walk":
		var target := Vector2(-1, -1)
		for m in state.get("missions", []):
			if str(m.get("status", "")) == "AVAILABLE": target = Vector2(float(m.get("x", 0.0)), float(m.get("z", 0.0)))
		if target.x < 0.0:
			print("VICE_BRIDGE_OK (no mission available to walk to)")
			get_tree().quit()
			return
		var s := my_sim_pos()
		if t < wait_before:
			pass
		elif s.distance_to(target) <= 1.5:
			if _prompt_action == "mission":
				_press(KEY_E)                   # the same key a player presses
				_check_stage = "await_mission"
				_check_started_at = _now()
		else:
			# Walk the local player straight at the job at 6 m/s (verification only; ignores city collision).
			var g := sim_to_city(target.x, target.y)
			var p: Vector3 = game.player_pos
			var d := Vector2(g.x - p.x, g.y - p.z)
			# Follow the street grid: close the north-south gap first, then east-west.
			if absf(d.y) > 0.3: d.x = 0.0
			else: d.y = 0.0
			var step := minf(d.length(), 6.0 * get_process_delta_time())
			var nd := d.normalized() * step
			game.player_pos = Vector3(p.x + nd.x, p.y, p.z + nd.y)
			if game.player_node != null:
				game.player_node.position = game.player_pos
				game.player_node.rotation.y = atan2(nd.x, nd.y)
			if d.length() > 0.5: game.cam_yaw = lerp_angle(game.cam_yaw, atan2(-d.x, -d.y), 0.08)
		if t > 60.0 + wait_before: _fail("walk to mission timed out; pose_rejects=%d sim=(%.1f,%.1f)" % [pose_rejects, s.x, s.y])
	elif _check_stage == "await_mission":
		if _my_mission_done:
			print("VICE_BRIDGE_MISSION_OK events=%d pose_rejects=%d feed=%s" % [events_seen, pose_rejects, str(_feed)])
			_shot_later("s1_job_done", 1.2)
			_check_stage = "linger"
			_check_started_at = _now()
		elif t > 8.0:
			_fail("mission.interact not confirmed by a MISSION_COMPLETED event")
	elif _check_stage == "hunt":
		var target_id := ""
		for o in state.get("characters", []):
			if str(o.get("id", "")) != my_character_id and str(o.get("role", "")) == "OUTLAW" and str(o.get("status", "")) in ["ACTIVE", "WANTED"]:
				target_id = str(o.get("id", ""))
		if _captured_by != "":
			if _captured_by == my_player_id:
				var w = state.get("wallet")
				print("VICE_BRIDGE_CAPTURE_OK by_me=true wallet=V$%s feed=%s" % [str(w.get("balanceCredits", "?")) if typeof(w) == TYPE_DICTIONARY else "?", str(_feed)])
			else:
				print("VICE_BRIDGE_CAPTURED_BY_OTHER captor=%s feed=%s" % [_npc_name(_captured_by), str(_feed)])
			_check_stage = "linger"
			_check_started_at = _now()
		elif target_id != "" and entities.has(target_id):
			var ts: Vector2 = entities[target_id].sim
			var gdist: float = my_sim_pos().distance_to(ts)
			if guns and gdist < 12.0 and gdist > 3.0 and _now() - _hunt_last_shot > 4.0:
				for o2 in state.get("characters", []):
					if str(o2.get("id", "")) == target_id and bool(o2.get("wanted", false)):
						_hunt_last_shot = _now()
						var gc := sim_to_city(ts.x, ts.y)
						var gd := Vector2(gc.x - game.player_pos.x, gc.y - game.player_pos.z)
						game.cam_yaw = atan2(-gd.x, -gd.y)
						fire_dir(gd.normalized())
			if _prompt_action == "capture":
				if _now() - _last_capture_try > 1.6:
					_last_capture_try = _now()
					_press(KEY_E)
			elif my_sim_pos().distance_to(ts) > 1.6:
				var gt := sim_to_city(ts.x, ts.y)
				var pp: Vector3 = game.player_pos
				var dd := Vector2(gt.x - pp.x, gt.y - pp.z)
				var st2 := minf(maxf(0.0, dd.length() - 1.2), 8.0 * get_process_delta_time())
				var nn := dd.normalized() * st2
				game.player_pos = Vector3(pp.x + nn.x, pp.y, pp.z + nn.y)
		if t > 150.0: _fail("no outlaw captured within 150 s (target=%s wanted_prompt=%s)" % [target_id, _prompt_action])
	elif _check_stage == "linger":
		var mec := _my_character()
		if record_dir != "": _direct_camera(mec)
		if not _wanted_shot and bool(mec.get("wanted", false)):
			_wanted_shot = true
			print("VICE_BRIDGE_WANTED heat=%d level=%d" % [int(mec.get("heat", 0)), int(mec.get("wantedLevel", 0))])
			_shot_later("s2_wanted", 0.4)
		if not _capture_shot and _captured_by != "" and str(mec.get("status", "")) == "CAPTURED":
			_capture_shot = true
			_capture_at = _now()
			var ml := {}
			for l in state.get("listings", []):
				if str(l.get("ticker", "")) == str(mec.get("ticker", "")): ml = l
			print("VICE_BRIDGE_CAPTURED my_listing_status=%s spot=%s captor=%s" % [str(ml.get("status", "?")), str(ml.get("spotPrice", "?")), _npc_name(_captured_by)])
			_shot_later("s3_captured_v0", 0.7)
			_old_character = my_character_id
			_old_ticker = str(mec.get("ticker", ""))
		if _capture_shot and not _respawn_pressed and _now() - _capture_at > 2.5:
			_respawn_pressed = true
			_press(KEY_R)
		if _respawn_pressed and not _respawn_done and my_character_id != _old_character and str(mec.get("status", "")) == "ACTIVE":
			_respawn_done = true
			_capture_at = _now()
			var old_status := "?"
			for l in state.get("listings", []):
				if str(l.get("ticker", "")) == _old_ticker: old_status = "%s@V$%d" % [str(l.get("status", "")), int(l.get("spotPrice", -1))]
			print("VICE_BRIDGE_RESPAWN_OK old=%s(%s %s) new=%s(%s)" % [_old_character, _old_ticker, old_status, my_character_id, str(mec.get("ticker", ""))])
			_shot_later("s4_respawned", 1.0)
		if (_respawn_done and _now() - _capture_at > 3.0) or (_capture_shot and _now() - _capture_at > 12.0) or t > maxf(6.0, linger):
			print("VICE_FEED ", str(_feed))
			_check_stage = "done"
	elif _check_stage == "done":
		_finish_recording()
		print("VICE_BRIDGE_OK ticks=%d events=%d" % [ticks_seen, events_seen])
		get_tree().quit()
		_check_stage = "quit"


func _shot(name: String) -> void:
	await RenderingServer.frame_post_draw
	var img := get_viewport().get_texture().get_image()
	var path := shots_dir.path_join(name + ".png")
	img.save_png(path)
	print("VICE_SHOT ", path)

func _stand_near(id: String, d: float) -> void:
	var e: Dictionary = entities[id]
	var best := Vector2(e.pos.x, e.pos.z + d)
	for i in 16:
		var a := float(i) / 16.0 * TAU + PI * 0.5
		var x: float = e.pos.x + cos(a) * d
		var z: float = e.pos.z + sin(a) * d
		# the chase camera sits ~6.5 m behind the player: keep that spot clear of walls too
		var cx: float = e.pos.x + cos(a) * (d + 7.0)
		var cz: float = e.pos.z + sin(a) * (d + 7.0)
		var mx: float = e.pos.x + cos(a) * (d + 3.5)
		var mz: float = e.pos.z + sin(a) * (d + 3.5)
		if not game.world.collides_at(x, z, 0.8) and not game.world.collides_at(cx, cz, 1.2) and not game.world.collides_at(mx, mz, 1.0):
			best = Vector2(x, z)
			break
	game.player_pos = Vector3(best.x, _ground(best.x, best.y), best.y)
	game.player_node.position = game.player_pos
	var dx: float = e.pos.x - best.x
	var dz: float = e.pos.z - best.y
	game.cam_yaw = atan2(-dx, -dz)
	game.cam_pitch = 0.12

## Shots mode with --vice-fx: stand by a witness, ask the server for an incident (director only),
## and photograph what the swarm does about it: popup, message arc, bulletin ring.
func _run_fx_shots() -> void:
	Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
	await get_tree().create_timer(2.0).timeout
	_stand_near("npc_w1", 6.0)
	await get_tree().create_timer(1.8).timeout          # let the server accept the new pose
	_send({"type": "demo.incident", "incidentType": "SIGHTING", "commandId": _cid("ginc")}, "incident")
	var tag := want_mode if want_mode != "" else "drex"
	# Wait for the first message arc, then watch it side-on so the whole flight is in frame.
	var waited := 0.0
	while _last_arc.is_empty() and waited < 6.0:
		await get_tree().process_frame
		waited += get_process_delta_time()
	var cam := Camera3D.new()
	cam.far = 3000.0
	cam.fov = 62.0
	game.add_child(cam)
	if not _last_arc.is_empty():
		var a: Vector3 = _last_arc.a
		var b: Vector3 = _last_arc.b
		var mid := (a + b) * 0.5
		var dir := Vector3(b.x - a.x, 0, b.z - a.z)
		var span := maxf(dir.length(), 1.0)
		dir = dir / span
		var best := mid + Vector3(-dir.z, 0, dir.x) * (span * 0.62 + 6.0)
		for sgn in [1.0, -1.0]:
			var cpos: Vector3 = mid + Vector3(-dir.z, 0, dir.x) * (span * 0.62 + 6.0) * float(sgn)
			if not game.world.collides_at(cpos.x, cpos.z, 2.0):
				best = cpos
				break
		cam.position = Vector3(best.x, 5.5, best.z)
		cam.look_at(Vector3(mid.x, 5.0, mid.z))
		cam.current = true
	for step in [["a", 0.35], ["b", 0.4], ["c", 0.45], ["d", 0.6], ["e", 0.9], ["f", 1.5]]:
		await get_tree().create_timer(step[1]).timeout
		await _shot("fx_%s_%s" % [tag, step[0]])
	print("VICE_SHOTS_OK fx feed=%s" % str(_feed))
	get_tree().quit()

func _run_shots() -> void:
	if fx_shots:
		_run_fx_shots()
		return
	Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
	await get_tree().create_timer(2.5).timeout
	await _shot("01_spawn")
	var i := 2
	var order: Array = ["npc_p1", "npc_w1", "npc_t1", "npc_i1", "npc_t2", "npc_p2"]
	for id in entities.keys():
		if entities[id].kind in ["OUTLAW", "HUNTER"]: order.push_front(id)
	for id in order:
		if not entities.has(id): continue
		_stand_near(id, 4.5)
		await get_tree().create_timer(1.4).timeout
		await _shot("%02d_%s" % [i, id])
		i += 1
	var cam := Camera3D.new()
	cam.far = 3000.0
	game.add_child(cam)
	var c := sim_to_city(47.0, 46.0)
	cam.position = Vector3(c.x, 46.0, c.y + 50.0)
	cam.look_at(Vector3(c.x, 0.0, c.y - 4.0))
	cam.current = true
	_overview = true
	await get_tree().create_timer(0.6).timeout
	await _shot("%02d_overview" % i)
	cam.queue_free()
	if game.camera != null: game.camera.current = true
	print("VICE_SHOTS_OK entities=%d ticks=%d" % [entities.size(), ticks_seen])
	get_tree().quit()


# ================================================================ guns, cars, helicopter
## Called by the city's own trigger (scripts/game.gd _fire_weapon, patched by apply.py). In VICE mode the
## local bullet, damage and wanted system never run: the shot is a server-side stun pulse, so nothing
## happens here that the server does not know about.
func fire(w: Dictionary) -> void:
	if not guns or not joined or my_character_id == "" or game == null: return
	if bool(w.get("melee", false)): return
	var dir: Vector3 = -game.camera.global_transform.basis.z
	fire_dir(Vector2(dir.x, dir.z), str(w.get("name", "PISTOL")))

func fire_dir(city_dir: Vector2, weapon_name := "PISTOL") -> void:
	var c := _my_character()
	var st := str(c.get("status", ""))
	if st != "ACTIVE" and st != "WANTED": return
	if _now() < _my_stun_until: return
	if _now() - _last_fire < 2.5:
		_set_notice("Pulse recharging…", 0.7)
		return
	var sd := Vector2(city_dir.x / SCALE.x, city_dir.y / SCALE.y)
	if sd.length() < 0.001: return
	sd = sd.normalized()
	_last_fire = _now()
	_send({"type": "weapon.fire", "dirX": snappedf(sd.x, 0.001), "dirZ": snappedf(sd.y, 0.001), "commandId": _cid("gwpn")}, "weapon")
	AudioFX.gunshot(weapon_name)
	Gamepad.pulse(0.55, 0.6, 0.16)
	var cd := Vector3(city_dir.x, 0, city_dir.y).normalized()
	_fx_flash(game.player_pos + Vector3(0, 1.45, 0) + cd * 0.9)

func _gun_txt(c: Dictionary) -> String:
	if not guns: return ""
	var ammo := my_ammo
	if c.get("ammo") != null: ammo = int(c.get("ammo"))
	var cool: float = 2.5 - (_now() - _last_fire)
	var txt := "   PULSE %s/6" % (str(ammo) if ammo >= 0 else "6")
	if _now() < _my_stun_until: txt += "  STUNNED"
	elif cool > 0.0: txt += "  recharging %.1fs" % cool
	else: txt += "  READY"
	return txt

func _fx_flash(at: Vector3) -> void:
	var m := _orb(Color("fff2a8"), 0.22)
	m.position = at
	game.add_child(m)
	var l := OmniLight3D.new()
	l.light_color = Color("ffd98a")
	l.light_energy = 6.0
	l.omni_range = 9.0
	l.shadow_enabled = false
	m.add_child(l)
	_tracers.append({"node": m, "t": 0.0, "life": 0.09})

func _fx_tracer(a_sim: Vector2, b_sim: Vector2, hit: bool) -> void:
	if game == null: return
	var a2 := sim_to_city(a_sim.x, a_sim.y)
	var b2 := sim_to_city(b_sim.x, b_sim.y)
	var a := Vector3(a2.x, _ground(a2.x, a2.y) + 1.4, a2.y)
	var b := Vector3(b2.x, _ground(b2.x, b2.y) + 1.2, b2.y)
	var seg := a.distance_to(b)
	if seg < 0.2: return
	var beam := MeshInstance3D.new()
	var bm := BoxMesh.new()
	bm.size = Vector3(0.09, 0.09, seg)
	beam.mesh = bm
	beam.material_override = _glow(Color("7dfff0") if hit else Color("ffd166"), 9.0)
	game.add_child(beam)
	beam.look_at_from_position((a + b) * 0.5, b, Vector3.UP)
	_tracers.append({"node": beam, "t": 0.0, "life": 0.45})
	_fx_flash(a)
	if hit:
		var ring := MeshInstance3D.new()
		var tm := TorusMesh.new()
		tm.inner_radius = 0.9
		tm.outer_radius = 1.05
		ring.mesh = tm
		ring.material_override = _glow(Color("7dfff0"), 8.0)
		ring.position = Vector3(b.x, b.y - 1.1, b.z)
		game.add_child(ring)
		_tracers.append({"node": ring, "t": 0.0, "life": 3.0, "grow": true})

func _update_weapons(dt: float) -> void:
	var keep: Array = []
	for f in _tracers:
		f.t += dt
		var n = f.node
		if not is_instance_valid(n): continue
		if f.t >= f.life:
			n.queue_free()
			continue
		if f.get("grow", false):
			var k: float = 1.0 + 0.25 * sin(f.t * 10.0)
			n.scale = Vector3(k, 1.0, k)
		keep.append(f)
	_tracers = keep
	# Knocked down: the local player is held in place and laid flat until the stun ends.
	if game != null and game.player_node != null:
		if _now() < _my_stun_until:
			if game.in_car == null:
				game.player_pos = _stun_pos
				game.player_node.position = _stun_pos + Vector3(0, 0.35, 0)
			game.player_node.rotation.x = -1.4
		elif absf(game.player_node.rotation.x) > 0.01:
			game.player_node.rotation.x = 0.0

## A remote player moving at driving speed is shown in a car instead of as a sprinting figure.
func _entity_car(e: Dictionary) -> void:
	if e.kind != "OUTLAW" and e.kind != "HUNTER": return
	var fast: bool = float(e.speed) > 13.0
	var slow: bool = float(e.speed) < 5.0
	if fast and not e.in_car:
		e.in_car = true
		if e.car == null and game.has_method("_make_vehicle"):
			var v = game._make_vehicle(e.pos.x, e.pos.z, game.VEHICLE_COLORS[0], "sedan")
			var cn: Node3D = v.node
			cn.get_parent().remove_child(cn)
			(e.node as Node3D).add_child(cn)
			cn.position = Vector3.ZERO
			cn.rotation = Vector3.ZERO
			e.car = cn
	elif slow and e.in_car:
		e.in_car = false
	if e.car != null: (e.car as Node3D).visible = e.in_car
	for b in e.body:
		if is_instance_valid(b): (b as Node3D).visible = not e.in_car

## One flyable helicopter on open ground inside the district (the city's own one sits on the airport island).
func _place_heli() -> void:
	if not heli_on or web_embed or game == null or not game.has_method("_make_helicopter"): return
	for s in [Vector2(62, 54), Vector2(92, 54), Vector2(32, 54), Vector2(62, 28), Vector2(92, 28)]:
		var g := sim_to_city(s.x, s.y)
		if game.world.collides_at(g.x, g.y, 4.5): continue
		var blocked := false
		for v in game.vehicles:
			if Vector2(v.pos.x - g.x, v.pos.z - g.y).length() < 9.0: blocked = true
		if blocked: continue
		_heli = game._make_helicopter(g.x, g.y, 0.0)
		game.vehicles.append(_heli)
		print("[vice] helicopter parked at city (%.1f, %.1f) = sim (%.0f, %.0f)" % [g.x, g.y, s.x, s.y])
		return
	print("[vice] no clear ground for the helicopter")

func _hold(keycode: int, on: bool) -> void:
	var ev := InputEventKey.new()
	ev.physical_keycode = keycode
	ev.keycode = keycode
	ev.pressed = on
	Input.parse_input_event(ev)

func _nearest_human() -> String:
	var best := ""
	var bd := 1e9
	var me := my_sim_pos()
	for id in entities.keys():
		var e: Dictionary = entities[id]
		if e.kind != "OUTLAW" and e.kind != "HUNTER": continue
		var d: float = me.distance_to(e.sim)
		if d < bd:
			bd = d
			best = id
	return best

## Scripted verification runs (two real clients against a real server). Every action goes through the
## same code a player's key reaches: fire_dir(), the city's _try_enter_exit(), and held movement keys.
func _run_demo(dt: float) -> void:
	if not (joined and have_state and ticks_seen >= 10 and _started_game): return
	Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
	_demo_t += dt
	match demo:
		"gun":
			var id := _nearest_human()
			if id == "":
				if _demo_t > 40.0: _fail("no other player to shoot at")
				return
			var e: Dictionary = entities[id]
			var me := my_sim_pos()
			var d: float = me.distance_to(e.sim)
			if _demo_stage == "":
				if d > 9.0:
					var g := sim_to_city(e.sim.x, e.sim.y)
					var to: Vector3 = Vector3(g.x, 0, g.y) - game.player_pos
					to.y = 0
					# test driver only: a straight line at 28 m/s (the server speed-checks bridge poses, nothing else)
					game.player_pos = game.player_pos + to.normalized() * minf(28.0 * dt, to.length())
					game.player_node.position = game.player_pos
					game.cam_yaw = atan2(-to.x, -to.z)
					if _demo_t > 60.0: _fail("could not reach the other player")
				else:
					_demo_stage = "fire"
					_demo_t = 0.0
					var g2 := sim_to_city(e.sim.x, e.sim.y)
					var dv := Vector2(g2.x - game.player_pos.x, g2.y - game.player_pos.z)
					game.cam_yaw = atan2(-dv.x, -dv.y)
					game.cam_pitch = 0.1
					fire_dir(dv)
					_shot_later("gun_1_fire", 0.12)
					_shot_later("gun_2_target_down", 1.2)
			elif _demo_stage == "fire" and _demo_t > 4.5:
				print("VICE_DEMO gun shots_seen=%d stuns_seen=%d ammo=%d pose_rejects=%d" % [_shots_seen, _stuns_seen, my_ammo, pose_rejects])
				if _stuns_seen > 0: print("VICE_DEMO_OK gun")
				else: print("VICE_DEMO_FAIL gun (no STUNNED event)")
				_demo_stage = "done"
				_demo_quit(0 if _stuns_seen > 0 else 1)
		"victim":
			if _demo_stage == "" and _now() < _my_stun_until:
				_demo_stage = "hit"
				_demo_t = 0.0
				_demo_p0 = my_sim_pos()
				_hold(KEY_W, true)
				_shot_later("victim_1_stunned", 0.6)
			elif _demo_stage == "hit" and _demo_t > 2.4:
				_hold(KEY_W, false)
				print("VICE_DEMO victim moved_while_stunned=%.2f m" % my_sim_pos().distance_to(_demo_p0))
				print("VICE_DEMO_OK victim")
				_demo_stage = "done"
				_demo_quit(0)
			elif _demo_t > 90.0: _fail("never stunned")
		"car":
			if _demo_stage == "":
				var best = null
				var bd := 1e9
				for v in game.vehicles:
					if v.get("is_plane", false) or v.get("is_boat", false): continue
					var s := city_to_sim(v.pos.x, v.pos.z)
					if s.x < 8 or s.x > 112 or s.y < 8 or s.y > 66: continue
					var dd: float = game.player_pos.distance_to(v.pos)
					if dd < bd:
						bd = dd
						best = v
				if best == null: return _fail("no car in the district")
				game.player_pos = best.pos + Vector3(2.2, 0, 0)
				game.player_node.position = game.player_pos
				_demo_stage = "settle"
				_demo_t = 0.0
			elif _demo_stage == "settle" and _demo_t > 1.2:
				game._try_enter_exit()
				print("VICE_DEMO car entered=%s" % str(game.in_car != null))
				if game.in_car == null: return _fail("could not enter the car")
				_demo_stage = "drive"
				_demo_t = 0.0
				_demo_p0 = my_sim_pos()
				_hold(KEY_W, true)
				_shot_later("car_1_driving", 2.5)
			elif _demo_stage == "drive" and _demo_t > 6.0:
				_hold(KEY_W, false)
				var moved: float = my_sim_pos().distance_to(_demo_p0)
				print("VICE_DEMO car drove=%.1f sim m in 6 s speed=%.1f pose_rejects=%d outside=%s" % [moved, float(game.in_car.get("speed", 0.0)) if game.in_car != null else 0.0, pose_rejects, str(outside_sector)])
				print("VICE_DEMO_OK car" if moved > 15.0 else "VICE_DEMO_FAIL car")
				_demo_stage = "done"
				_demo_quit(0 if moved > 15.0 else 1)
		"watch":
			var wid := _nearest_human()
			if wid != "":
				var we: Dictionary = entities[wid]
				if _demo_stage == "" and we.in_car:
					_demo_stage = "seen"
					_demo_t = 0.0
					game.player_pos = Vector3(we.pos.x + 7.0, 0, we.pos.z + 7.0)
					game.player_node.position = game.player_pos
					game.cam_yaw = atan2(7.0, 7.0)
					print("VICE_DEMO watch remote_in_car speed=%.1f sim m/s" % float(we.speed))
					_shot_later("watch_1_remote_car", 0.4)
				elif _demo_stage == "seen" and _demo_t > 1.5:
					print("VICE_DEMO_OK watch")
					_demo_stage = "done"
					_demo_quit(0)
			if _demo_t > 90.0: _fail("never saw the remote player in a car")
		"heli":
			if _demo_stage == "":
				if _heli == null: return _fail("no helicopter placed")
				game.player_pos = _heli.pos + Vector3(3.0, 0, 0)
				game.player_node.position = game.player_pos
				_demo_stage = "settle"
				_demo_t = 0.0
			elif _demo_stage == "settle" and _demo_t > 1.2:
				_shot("heli_0_parked")
				game._try_enter_exit()
				print("VICE_DEMO heli boarded=%s" % str(game.in_car != null and game.in_car.get("is_heli", false)))
				if game.in_car == null: return _fail("could not board the helicopter")
				_demo_stage = "climb"
				_demo_t = 0.0
				_hold(KEY_UP, true)
				_shot_later("heli_1_airborne", 3.0)
			elif _demo_stage == "climb" and _demo_t > 5.0:
				_hold(KEY_UP, false)
				var alt: float = float(game.in_car.pos.y) if game.in_car != null else 0.0
				print("VICE_DEMO heli altitude=%.1f m pose_rejects=%d" % [alt, pose_rejects])
				print("VICE_DEMO_OK heli" if alt > 5.0 else "VICE_DEMO_FAIL heli")
				_demo_stage = "done"
				_demo_quit(0 if alt > 5.0 else 1)

func _demo_quit(code: int) -> void:
	_finish_recording()
	get_tree().quit(code)
