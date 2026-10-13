fn main() {
    // Compile the Slint UI with the bundled translations (ADR-0032).
    let config = slint_build::CompilerConfiguration::new()
        .with_bundled_translations("lang")
        .with_default_translation_context(slint_build::DefaultTranslationContext::None);
    slint_build::compile_with_config("ui/app.slint", config).expect("Slint UI must compile");
    println!("cargo:rerun-if-changed=lang");
}
