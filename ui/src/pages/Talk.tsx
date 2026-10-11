import { PageHeader, SaveStatus } from "../components/ui";
import { t } from "../i18n";
import { useConfig } from "../store";
import { OutputSections } from "./Output";
import { VoiceSections } from "./Voice";

/** Voz y respuestas: cómo te contesta y cómo le pides consejo, con una sola configuración compartida. */
export function Talk() {
  const { config, setConfig, save, error, saved, saving } = useConfig();
  if (!config) return null;
  const props = { config, setConfig, save };
  return (
    <>
      <PageHeader title={t("talk.title")} subtitle={t("talk.subtitle")} />
      <OutputSections {...props} />
      <VoiceSections {...props} />
      <SaveStatus saving={saving} saved={saved} error={error} />
    </>
  );
}
