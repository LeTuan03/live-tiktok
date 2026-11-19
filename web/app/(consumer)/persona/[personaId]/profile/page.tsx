import { PersonaProfile } from "@/components/persona/PersonaProfile";
import {
  DefaultPersonaProfileService,
  describeLoadError,
  type PersonaClip,
  type PersonaProduct,
  type PersonaProfile as PersonaProfileData,
} from "@/lib/persona/PersonaProfileService";

// `/persona/[personaId]/profile`: server component that loads the static
// parts (bio, recent shows, products) on the request thread and hands
// them down to the interactive client shell. The backend is open
// access, so the profile renders for everyone.

interface PersonaProfilePageProps {
  // Next.js 15: dynamic route params arrive as a Promise.
  params: Promise<{ personaId: string }>;
}

export default async function PersonaProfilePage({ params }: PersonaProfilePageProps) {
  const { personaId } = await params;

  return <LoadedProfile personaId={personaId} />;
}

async function LoadedProfile({ personaId }: { personaId: string }) {
  const service = new DefaultPersonaProfileService();

  let profile: PersonaProfileData | null = null;
  let clips: PersonaClip[] = [];
  let products: PersonaProduct[] = [];
  let loadErrorMessage: string | null = null;

  try {
    // Issue the profile + clips + products fetches in parallel. A
    // single failure collapses to the error path; partial-success
    // rendering is not worth the extra complexity here.
    const [p, c, prod] = await Promise.all([
      service.fetchProfile(personaId),
      service.fetchClips(personaId),
      service.fetchProducts(personaId),
    ]);
    profile = p;
    clips = c;
    products = prod;
  } catch (err) {
    loadErrorMessage = describeLoadError(err);
  }

  return (
    <PersonaProfile
      personaId={personaId}
      profile={profile}
      clips={clips}
      products={products}
      loadErrorMessage={loadErrorMessage}
    />
  );
}
