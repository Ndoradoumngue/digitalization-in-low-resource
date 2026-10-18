import { useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { deleteAvatar, updateProfile, uploadAvatar } from "@sdai/api-client";
import NavSidebar from "../components/NavSidebar";
import Avatar from "../components/Avatar";
import { useAuth } from "../context/AuthContext";

const MAX_PICTURE_MB = 5;

// "Mon profil": what a person may change about themselves - picture, name,
// job title, phone (profile_router). E-mail, role and permissions stay with
// the user managers.
export default function ProfilePage() {
  const { t } = useTranslation();
  const { user, refresh } = useAuth();
  const fileInput = useRef<HTMLInputElement>(null);
  const [fullName, setFullName] = useState(user?.full_name ?? "");
  const [jobTitle, setJobTitle] = useState(user?.job_title ?? "");
  const [phone, setPhone] = useState(user?.phone ?? "");
  const [busy, setBusy] = useState(false);
  const [pictureBusy, setPictureBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pictureError, setPictureError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [pictureStatus, setPictureStatus] = useState<string | null>(null);

  const dirty =
    fullName.trim() !== (user?.full_name ?? "") ||
    jobTitle.trim() !== (user?.job_title ?? "") ||
    phone.trim() !== (user?.phone ?? "");

  async function save(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setStatus(null);
    setBusy(true);
    try {
      const saved = await updateProfile({ full_name: fullName, job_title: jobTitle, phone });
      setFullName(saved.full_name ?? "");
      setJobTitle(saved.job_title ?? "");
      setPhone(saved.phone ?? "");
      await refresh();
      setStatus(t("profile.saved"));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function pictureChosen(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setPictureError(null);
    setPictureStatus(null);
    if (file.size > MAX_PICTURE_MB * 1024 * 1024) {
      return setPictureError(t("profile.tooLarge", { n: MAX_PICTURE_MB }));
    }
    setPictureBusy(true);
    try {
      await uploadAvatar(file);
      await refresh();
      setPictureStatus(t("profile.pictureSaved"));
    } catch (err) {
      setPictureError((err as Error).message);
    } finally {
      setPictureBusy(false);
    }
  }

  async function removePicture() {
    setPictureError(null);
    setPictureStatus(null);
    setPictureBusy(true);
    try {
      await deleteAvatar();
      await refresh();
      setPictureStatus(t("profile.pictureRemoved"));
    } catch (err) {
      setPictureError((err as Error).message);
    } finally {
      setPictureBusy(false);
    }
  }

  const input =
    "w-full h-9 rounded-md border border-gray-300 px-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400";
  const card = "bg-white rounded-lg border border-gray-200 p-5 max-w-md";
  const heading = "text-sm font-semibold text-gray-900 mb-3";

  return (
    <div className="flex h-screen bg-gray-50 font-sans text-gray-900 overflow-hidden">
      <NavSidebar />
      <main className="flex-1 overflow-y-auto p-6 space-y-4">
        <h1 className="text-lg font-bold text-gray-900">{t("profile.title")}</h1>

        <section className={card}>
          <h2 className={heading}>{t("profile.picture")}</h2>
          <div className="flex items-center gap-4">
            <Avatar url={user?.avatar_url} name={user?.full_name} email={user?.email} size="lg" />
            <div className="space-y-2">
              <div className="flex items-center gap-3">
                <button type="button" disabled={pictureBusy} onClick={() => fileInput.current?.click()}
                  className="h-9 px-4 rounded-md bg-indigo-600 text-white text-sm font-semibold hover:bg-indigo-700 disabled:opacity-50">
                  {pictureBusy ? t("profile.saving") : t("profile.choosePicture")}
                </button>
                {user?.avatar_url && (
                  <button type="button" disabled={pictureBusy} onClick={removePicture}
                    className="text-sm text-gray-500 hover:text-red-600 disabled:opacity-50">
                    {t("profile.removePicture")}
                  </button>
                )}
              </div>
              <input ref={fileInput} type="file" className="hidden" onChange={pictureChosen}
                accept="image/jpeg,image/png,image/webp,image/gif" />
              <p className="text-xs text-gray-500">{t("profile.pictureHint")}</p>
              {pictureError && <p role="alert" className="text-sm text-red-600">{pictureError}</p>}
              {pictureStatus && <p role="status" className="text-sm text-emerald-700">{pictureStatus}</p>}
            </div>
          </div>
        </section>

        <form onSubmit={save} className={`${card} space-y-4`}>
          <h2 className={heading}>{t("profile.basicInfo")}</h2>
          <label className="block text-sm">
            <span className="font-medium text-gray-700">{t("profile.fullName")}</span>
            <input autoComplete="name" maxLength={200} value={fullName}
              onChange={(e) => setFullName(e.target.value)} className={`${input} mt-1`} />
          </label>
          <label className="block text-sm">
            <span className="font-medium text-gray-700">{t("profile.jobTitle")}</span>
            <input autoComplete="organization-title" maxLength={200} value={jobTitle}
              onChange={(e) => setJobTitle(e.target.value)} className={`${input} mt-1`} />
          </label>
          <label className="block text-sm">
            <span className="font-medium text-gray-700">{t("profile.phone")}</span>
            <input type="tel" autoComplete="tel" maxLength={50} value={phone}
              onChange={(e) => setPhone(e.target.value)} className={`${input} mt-1`} />
          </label>
          <div>
            <label className="block text-sm">
              <span className="font-medium text-gray-700">{t("profile.email")}</span>
              <input value={user?.email ?? ""} readOnly aria-describedby="email-hint"
                className={`${input} mt-1 bg-gray-50 text-gray-500`} />
            </label>
            <p id="email-hint" className="text-xs text-gray-500 mt-0.5">{t("profile.emailHint")}</p>
          </div>
          {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
          {status && <p role="status" className="text-sm text-emerald-700">{status}</p>}
          <button type="submit" disabled={busy || !dirty}
            className="h-9 px-4 rounded-md bg-indigo-600 text-white text-sm font-semibold hover:bg-indigo-700 disabled:opacity-50">
            {busy ? t("profile.saving") : t("profile.save")}
          </button>
        </form>

        <section className={card}>
          <h2 className={heading}>{t("profile.password")}</h2>
          <Link to="/account/password" className="text-sm font-medium text-indigo-700 hover:underline">
            {t("profile.changePassword")}
          </Link>
        </section>
      </main>
    </div>
  );
}
