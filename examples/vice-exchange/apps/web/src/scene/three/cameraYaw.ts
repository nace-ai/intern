/**
 * The 3D follow camera's current ground-plane yaw (radians), written every frame by ThreeCity's FollowCamera.
 * 0 = lens south of the player looking north, so "forward" is north exactly as in the 2D view.
 * Kept in its own tiny module (no three.js import) so the keyboard handler can read it without loading the 3D bundle.
 */
export const cameraYaw = { value: 0, active: false };

/** Rotates a screen-relative key vector (x = right, z = toward the camera) into world axes for the current camera yaw. */
export function cameraRelative(x: number, z: number, yaw: number): { x: number; z: number } {
  const c = Math.cos(yaw), s = Math.sin(yaw);
  let wx = x * c + z * s, wz = -x * s + z * c;
  const len = Math.hypot(wx, wz);
  if (len > 1) { wx /= len; wz /= len; }
  return { x: Math.abs(wx) < 1e-6 ? 0 : wx, z: Math.abs(wz) < 1e-6 ? 0 : wz };
}
