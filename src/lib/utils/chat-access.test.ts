import { describe, expect, it } from 'vitest';
import { canUseTemporaryChat } from './chat-access';

describe('canUseTemporaryChat', () => {
	it('lets administrators use temporary chats regardless of global and group settings', () => {
		expect(canUseTemporaryChat('admin', false, false)).toBe(true);
	});
	it('gives the global switch priority over user permissions', () => {
		expect(canUseTemporaryChat('user', false, true)).toBe(false);
	});
	it('preserves group restrictions when globally enabled', () => {
		expect(canUseTemporaryChat('user', true, false)).toBe(false);
	});
	it('preserves existing defaults for older servers', () => {
		expect(canUseTemporaryChat('user', undefined, undefined)).toBe(true);
	});
});
