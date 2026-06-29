import { redirect } from "next/navigation";

// La raíz redirige al módulo OMNICANAL (la sección activa de esta versión).
export default function Home() {
  redirect("/omnicanal");
}
