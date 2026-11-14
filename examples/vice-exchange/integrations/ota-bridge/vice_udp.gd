extends RefCounted
# VICE EXCHANGE UDP fast path (client side). OWNER: UDP. Installed next to vice_bridge.gd by apply.py.
# The WebSocket stays the reliable channel. Over UDP this client sends its pose (up to 30 Hz) and receives the
# world state (30 Hz). Server side and wire format: apps/server/src/bridge/udp.ts.
#   out:  <32 hex token>{"q":seq,"x":..,"z":..,"h":heading,"f":flags}     flags: 1 in a car, 2 airborne
#   in:   {"q":seq,"t":serverTimeMs,"hz":30,"c":[[id,x,z,heat,status,h,f]...],"n":[[id,x,z,activity,pending,stunned]...]}
# If no datagram comes back within CONFIRM_S of the offer, or the stream goes quiet for STALE_S, `active` is false
# and the bridge keeps using (or returns to) the 10 Hz WebSocket ticks.

const CONFIRM_S := 2.0
const STALE_S := 1.5
const HELLO_EVERY_S := 0.5
const POSE_INTERVAL := 1.0 / 30.0

var peer: PacketPeerUDP = null
var token := ""
var host := ""
var port := 0
var hz := 30
var offered := false      # the server offered a UDP channel
var active := false       # server datagrams are arriving
var gave_up := false      # offer timed out: staying on WebSocket
var _offered_at := 0.0
var _hello_at := -10.0
var _seq_out := 0
var _last_in := -1
var _first_in := -1
var received := 0
var _last_rx := 0.0
var state_age_ms := 0.0
var _rate_n := 0
var _rate_t0 := 0.0
var rx_hz := 0.0
var _log_n := 0
var _extra: Dictionary = {}   # character id -> [heading or null, flags]


func start(h: String, p: int, tok: String, state_hz: int, now: float) -> void:
	stop()
	host = h
	port = p
	token = tok
	hz = state_hz
	peer = PacketPeerUDP.new()
	var err := peer.connect_to_host(host, port)
	if err != OK:
		print("[vice] udp: cannot open socket to %s:%d (error %d), staying on WebSocket" % [host, port, err])
		peer = null
		return
	offered = true
	gave_up = false
	_offered_at = now
	_hello_at = -10.0
	_last_in = -1
	_first_in = -1
	received = 0
	_rate_n = 0
	_rate_t0 = now
	print("[vice] udp: offered %s:%d, announcing" % [host, port])


func stop() -> void:
	if peer != null: peer.close()
	peer = null
	offered = false
	active = false


func _put(body: Dictionary) -> void:
	if peer == null: return
	peer.put_packet((token + JSON.stringify(body)).to_utf8_buffer())


# Drains the socket. Returns the newest world state as a tick-shaped Dictionary ({characters, npcs}) or {} if none.
func poll(now: float) -> Dictionary:
	if peer == null: return {}
	if not active and now - _hello_at >= HELLO_EVERY_S:
		_hello_at = now
		_put({"hello": 1})
	var newest: Dictionary = {}
	while peer.get_available_packet_count() > 0:
		var pkt := peer.get_packet()
		var msg = JSON.parse_string(pkt.get_string_from_utf8())
		if typeof(msg) != TYPE_DICTIONARY: continue
		var q := int(msg.get("q", -1))
		if q <= _last_in: continue   # stale or duplicate
		if _first_in < 0: _first_in = q
		_last_in = q
		received += 1
		_rate_n += 1
		_last_rx = now
		newest = msg
	if not newest.is_empty():
		if not active:
			active = true
			gave_up = false
			print("[vice] udp: live, world state at %d Hz" % hz)
	else:
		if active and now - _last_rx > STALE_S:
			active = false
			_hello_at = -10.0
			print("[vice] udp: stream went quiet, back on WebSocket ticks")
		elif not active and not gave_up and now - _offered_at > CONFIRM_S:
			gave_up = true
			print("[vice] udp: no datagram from the server within %.0f s, staying on WebSocket" % CONFIRM_S)
	if now - _rate_t0 >= 1.0:
		rx_hz = float(_rate_n) / (now - _rate_t0)
		_rate_n = 0
		_rate_t0 = now
		_log_n += 1
		if active and _log_n % 3 == 0: print("[vice] udp: rx %.1f Hz, loss %.1f%%, poses sent %d" % [rx_hz, loss_pct(), _seq_out])
	state_age_ms = (now - _last_rx) * 1000.0 if received > 0 else 0.0
	if newest.is_empty(): return {}
	var chars: Array = []
	for c in newest.get("c", []):
		if typeof(c) != TYPE_ARRAY or c.size() < 5: continue
		chars.append({"id": str(c[0]), "x": float(c[1]), "z": float(c[2]), "heat": float(c[3]), "status": str(c[4])})
		if c.size() >= 7: _extra[str(c[0])] = [c[5], int(c[6])]
	var npcs: Array = []
	for n in newest.get("n", []):
		if typeof(n) != TYPE_ARRAY or n.size() < 5: continue
		npcs.append({"id": str(n[0]), "x": float(n[1]), "z": float(n[2]), "activity": str(n[3]), "pending": int(n[4]) == 1, "stunned": n.size() >= 6 and int(n[5]) == 1})
	return {"t": "tick", "characters": chars, "npcs": npcs, "udp": true}


func send_pose(x: float, z: float, heading: float, flags: int) -> void:
	_seq_out += 1
	_put({"q": _seq_out, "x": snappedf(x, 0.01), "z": snappedf(z, 0.01), "h": snappedf(heading, 0.01), "f": flags})


# Flags another client reported for a character: bit 1 in a car, bit 2 airborne. 0 when unknown.
func flags_of(character_id: String) -> int:
	return int(_extra[character_id][1]) if _extra.has(character_id) else 0


func loss_pct() -> float:
	if received <= 0 or _first_in < 0: return 0.0
	var expected := _last_in - _first_in + 1
	return clampf(100.0 * float(expected - received) / float(maxi(1, expected)), 0.0, 100.0)


func hud_text() -> String:
	if active: return "NET: UDP %d Hz  loss %.0f%%  age %d ms" % [int(round(rx_hz)) if rx_hz > 0.0 else hz, loss_pct(), int(state_age_ms)]
	return "NET: WS 10 Hz"
