/** Compare instants, not their timezone spelling. Unknown times sort first in
 * ascending order; equal/unknown times retain the input order in stable sorts.
 */
export function workTime(value?: string | null) {
  const parsed = value ? Date.parse(value) : NaN;
  return Number.isFinite(parsed) ? parsed : Number.MIN_SAFE_INTEGER;
}
