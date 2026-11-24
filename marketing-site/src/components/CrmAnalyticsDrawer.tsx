type Props = { open: boolean; heatmapOverlayEnabled: boolean; onToggleHeatmapOverlay: () => void; onClose: () => void; };
export default function CrmAnalyticsDrawer({open,onClose}: Props) {
 if (!open) return null;
 return <div role="dialog" aria-modal="true" aria-label="Private workspace" style={{position:'fixed',inset:0,zIndex:200,background:'#0f172aee',color:'#fff',padding:'10vh 10vw'}}><h2>Private CRM workspace</h2><p>Buyer records and analytics are available only after signing in to the operator console.</p><a href="/#/leads">Open CRM sign-in</a><button onClick={onClose} style={{marginLeft:24}}>Close</button></div>;
}
