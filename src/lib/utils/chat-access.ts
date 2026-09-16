export const canUseTemporaryChat = (
	role: string | undefined,
	globallyEnabled: boolean | undefined,
	userPermission: boolean | undefined
) => role === 'admin' || (globallyEnabled !== false && userPermission !== false);
