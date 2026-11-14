extends RefCounted
# VICE EXCHANGE world stream (publisher side). OWNER: ONEWORLD. Installed next to vice_bridge.gd by apply.py.
# With --vice-stream this ONE running game sends its own rendered frames to the server (ws /stream?role=pub&token=...)
# so browsers show them in the city pane, and it accepts keyboard / mouse input from the same player's browser.
#   out (binary): [u32 seq LE][f64 unix ms LE][JPEG bytes]
#   in  (text):   {"t":"k","code":"KeyW","down":true} | {"t":"m","dx":..,"dy":..} | {"t":"b","button":0,"down":true}
# Server side: apps/server/src/bridge/stream.ts.

const MAX_BUFFERED := 400000      # bytes waiting in the socket: skip frames instead of queueing (adaptive rate)

var ws: WebSocketPeer = null
var url := ""
var fps := 30.0
var width := 960
var quality := 0.7
var viewers := 0
var sent := 0
var active := false
var _seq := 0
var _next_at := 0.0
var _retry_at := 0.0
var _rate_n := 0
var _rate_t0 := 0.0
var tx_fps := 0.0
var _mouse := Vector2(640, 360)
var _busy := false
var _out := PackedByteArray()
var _mutex := Mutex.new()


func start(bridge_url: String, token: String) -> void:
	var base := bridge_url
	var i := base.rfind("/bridge")
	if i >= 0: base = base.substr(0, i)
	url = "%s/stream?role=pub&token=%s" % [base, token]
	_connect()


func _connect() -> void:
	ws = WebSocketPeer.new()
	ws.outbound_buffer_size = 4 * 1024 * 1024
	ws.max_queued_packets = 64
	var err := ws.connect_to_url(url)
	if err != OK: ws = null


func hud_text() -> String:
	if not active: return "STREAM: connecting"
	return "STREAM: %d fps to %d viewer%s" % [int(round(tx_fps)), viewers, "" if viewers == 1 else "s"]


func poll(now: float, viewport: Viewport) -> void:
	if ws == null:
		if now >= _retry_at:
			_retry_at = now + 2.0
			if url != "": _connect()
		return
	ws.poll()
	var st := ws.get_ready_state()
	if st == WebSocketPeer.STATE_CLOSED:
		active = false
		ws = null
		_retry_at = now + 2.0
		return
	if st != WebSocketPeer.STATE_OPEN: return
	active = true
	while ws.get_available_packet_count() > 0:
		var pkt := ws.get_packet()
		if ws.was_string_packet(): _on_text(pkt.get_string_from_utf8())
	if viewers <= 0 and sent > 0: return                       # nobody is watching: do not burn frames
	if ws.get_current_outbound_buffered_amount() > MAX_BUFFERED: return   # still draining: skip this frame
	if _busy: return                                             # the previous frame is still being encoded
	if _out.size() > 0:
		_mutex.lock()
		var out: PackedByteArray = _out
		_out = PackedByteArray()
		_mutex.unlock()
		if ws.send(out, WebSocketPeer.WRITE_MODE_BINARY) == OK:
			sent += 1
			_rate_n += 1
	if now - _rate_t0 >= 1.0:
		tx_fps = float(_rate_n) / max(0.001, now - _rate_t0)
		_rate_n = 0
		_rate_t0 = now
	if now < _next_at: return
	_next_at = now + 1.0 / fps
	var img: Image = viewport.get_texture().get_image()
	if img == null or img.is_empty(): return
	_busy = true
	_seq += 1
	WorkerThreadPool.add_task(_encode.bind(img, _seq, Time.get_unix_time_from_system() * 1000.0))


# Runs on a worker thread: downscale and JPEG-encode off the main thread so the game keeps its frame rate.
func _encode(img: Image, seq: int, stamp_ms: float) -> void:
	if img.get_width() > width:
		img.resize(width, int(round(float(img.get_height()) * float(width) / float(img.get_width()))), Image.INTERPOLATE_BILINEAR)
	var jpg: PackedByteArray = img.save_jpg_to_buffer(quality)
	var head := StreamPeerBuffer.new()
	head.big_endian = false
	head.put_u32(seq)
	head.put_double(stamp_ms)
	var out: PackedByteArray = head.data_array
	out.append_array(jpg)
	_mutex.lock()
	_out = out
	_mutex.unlock()
	_busy = false


func _on_text(text: String) -> void:
	var m = JSON.parse_string(text)
	if typeof(m) != TYPE_DICTIONARY: return
	var t := str(m.get("t", ""))
	if t == "viewers":
		viewers = int(m.get("n", 0))
	elif t == "k":
		var code := _keycode(str(m.get("code", "")))
		if code == KEY_NONE: return
		var ev := InputEventKey.new()
		ev.keycode = code
		ev.physical_keycode = code
		ev.pressed = bool(m.get("down", false))
		Input.parse_input_event(ev)
	elif t == "m":
		var ev := InputEventMouseMotion.new()
		ev.relative = Vector2(clampf(float(m.get("dx", 0.0)), -400.0, 400.0), clampf(float(m.get("dy", 0.0)), -400.0, 400.0))
		_mouse += ev.relative
		ev.position = _mouse
		ev.global_position = _mouse
		Input.parse_input_event(ev)
	elif t == "b":
		var ev := InputEventMouseButton.new()
		ev.button_index = MOUSE_BUTTON_RIGHT if int(m.get("button", 0)) == 2 else MOUSE_BUTTON_LEFT
		ev.pressed = bool(m.get("down", false))
		ev.position = _mouse
		ev.global_position = _mouse
		Input.parse_input_event(ev)


func _keycode(code: String) -> Key:
	# Browser KeyboardEvent.code -> Godot key. Escape is left to the browser (it releases the mouse there).
	if code.begins_with("Key") and code.length() == 4: return OS.find_keycode_from_string(code.substr(3))
	if code.begins_with("Digit") and code.length() == 6: return OS.find_keycode_from_string(code.substr(5))
	match code:
		"Space": return KEY_SPACE
		"ShiftLeft", "ShiftRight": return KEY_SHIFT
		"ControlLeft", "ControlRight": return KEY_CTRL
		"ArrowUp": return KEY_UP
		"ArrowDown": return KEY_DOWN
		"ArrowLeft": return KEY_LEFT
		"ArrowRight": return KEY_RIGHT
		"Tab": return KEY_TAB
		"Enter": return KEY_ENTER
		"BracketLeft": return KEY_BRACKETLEFT
		"BracketRight": return KEY_BRACKETRIGHT
	return KEY_NONE
