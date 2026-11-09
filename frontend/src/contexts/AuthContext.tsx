import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { type User, onAuthStateChanged } from "firebase/auth";
import { auth } from "../firebase";
import { api } from "../services/api";

interface AuthValue {
  user: User | null;
  uid: string | null;
  ready: boolean;
  loading: boolean;
  pageName: string;
  updatePageName: (name: string) => Promise<void>;
}

const AuthCtx = createContext<AuthValue>({
  user: null,
  uid: null,
  ready: false,
  loading: true,
  pageName: "",
  updatePageName: async () => undefined,
});

export const useAuth = () => useContext(AuthCtx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [pageName, setPageName] = useState("");

  useEffect(() => {
    const unsub = onAuthStateChanged(auth, (u) => {
      setUser(u);
      setLoading(false);
    });
    return unsub;
  }, []);

  useEffect(() => {
    if (!user) {
      setPageName("");
      return;
    }
    let alive = true;
    api
      .getProfile()
      .then((p) => alive && setPageName(p.page_name || ""))
      .catch(() => alive && setPageName(""));
    return () => {
      alive = false;
    };
  }, [user?.uid]); // eslint-disable-line react-hooks/exhaustive-deps

  async function updatePageName(name: string) {
    const { page_name } = await api.saveProfile(name.trim());
    setPageName(page_name || "");
  }

  const ready = !loading && user !== null;

  return (
    <AuthCtx.Provider
      value={{ user, uid: user?.uid ?? null, ready, loading, pageName, updatePageName }}
    >
      {children}
    </AuthCtx.Provider>
  );
}