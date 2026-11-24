import type { EstateProject } from '../data/projects';
import type { LeadSubmissionResult } from '../components/LeadCaptureModal';
type Props = {project:EstateProject;confirmation:LeadSubmissionResult|null;onNavigate:(path:string)=>void};
export default function ThankYouPage({project,confirmation,onNavigate}:Props) {
 return <section className="min-h-[75svh] bg-[#F6F5F0] px-6 py-24 dark:bg-[#080C0A]"><div className="mx-auto max-w-3xl rounded-2xl border border-[#D4AF5F]/40 bg-white p-10 dark:bg-[#101814]">
 <h1 className="text-3xl text-[#102018] dark:text-white">{confirmation ? 'Your enquiry has been received' : 'Enquiry status'}</h1>
 <p className="mt-6 text-[#31513c] dark:text-[#d6dfd7]">{confirmation ? `Thank you, ${confirmation.fullName}. An advisor will review your request for ${project.name}.` : 'Submit the enquiry form to receive a reference. This page alone does not confirm a submission.'}</p>
 {confirmation && <p className="mt-4 text-[#31513c] dark:text-[#d6dfd7]">Reference: {confirmation.referenceCode}</p>}
 <p className="mt-6 text-[#31513c] dark:text-[#d6dfd7]">A requested visit time is not a confirmed booking. Availability, pricing, title documents and appointment details require advisor verification. No email or WhatsApp delivery is implied by this confirmation.</p>
 <button className="mt-8 rounded bg-[#D4AF5F] px-5 py-3 text-[#102018]" onClick={()=>onNavigate('/projects/'+project.slug)}>Return to project</button>
 </div></section>;
}
