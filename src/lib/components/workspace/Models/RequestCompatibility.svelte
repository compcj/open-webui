<script lang="ts">
	import { getContext } from 'svelte';
	import ModelSettingToggle from './ModelSettingToggle.svelte';

	const i18n: any = getContext('i18n');

	export let reasoningContentReplay = true;
	export let extraBodyCompatibility = false;
	export let disabled = false;
</script>

<div class="my-3 space-y-1">
	<ModelSettingToggle
		label={$i18n.t('Replay reasoning content')}
		description={$i18n.t(
			"Replay the provider's original reasoning_content in later requests. Enabled by default."
		)}
		bind:checked={reasoningContentReplay}
		{disabled}
	/>
	<ModelSettingToggle
		label={$i18n.t('Extra body compatibility')}
		description={$i18n.t(
			'Expand extra_body fields into the request body for OpenAI SDK compatibility. Explicit top-level fields take precedence. Disabled by default.'
		)}
		bind:checked={extraBodyCompatibility}
		{disabled}
	/>
</div>
