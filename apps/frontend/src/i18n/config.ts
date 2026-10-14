import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import en from "./locales/en.json";
import fr from "./locales/fr.json";

const SUPPORTED = ["en", "fr"];

// Language of the deployment (DEFAULT_LANGUAGE in .env - the frontend
// container writes it into index.html at start; VITE_DEFAULT_LANGUAGE in
// development): used until the person picks one, whatever their browser
// says. Unset: the browser language, else English.
const configured =
  document.querySelector<HTMLMetaElement>('meta[name="sdai-default-language"]')?.content ||
  import.meta.env.VITE_DEFAULT_LANGUAGE;
const deploymentDefault = SUPPORTED.find((l) => l === configured);

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
      fr: { translation: fr },
    },
    fallbackLng: deploymentDefault ?? "en",
    supportedLngs: SUPPORTED,
    interpolation: { escapeValue: false },
    detection: {
      order: deploymentDefault ? ["localStorage"] : ["localStorage", "navigator"],
      caches: ["localStorage"],
      lookupLocalStorage: "sdai_lang",
    },
  });

export default i18n;
