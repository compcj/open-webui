type SlashMentionProps = {
	id: string;
	label: string;
	[key: string]: unknown;
};

export const buildSlashMentionContent = (props: SlashMentionProps) => [
	{
		type: 'mention',
		attrs: { ...props, mentionSuggestionChar: '/' }
	},
	{ type: 'text', text: ' ' }
];
