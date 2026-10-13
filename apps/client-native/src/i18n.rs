//! Interface language. Window text is translated by Slint `@tr()` with bundled `.po` files;
//! this module keeps the language choice and the few catalog strings Rust supplies.

#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum Language {
    #[default]
    Ko,
    En,
}

impl Language {
    /// Locale code used by `slint::select_bundled_translation` ("en" selects the source text).
    pub fn code(self) -> &'static str {
        match self {
            Self::Ko => "ko",
            Self::En => "en",
        }
    }

    pub fn from_code(code: &str) -> Option<Self> {
        match code {
            "ko" | "ko-KR" => Some(Self::Ko),
            "en" | "en-US" => Some(Self::En),
            _ => None,
        }
    }
}

/// Detects the OS display language with cross-platform fallback.
pub fn detect_os_language() -> Language {
    #[cfg(windows)]
    {
        // 0x0412 is the Korean (ko-KR) LANGID.
        let lcid = unsafe { windows_sys::Win32::Globalization::GetUserDefaultUILanguage() };
        if lcid == 0x0412 {
            Language::Ko
        } else {
            Language::En
        }
    }
    #[cfg(not(windows))]
    {
        let lang = std::env::var("LANG")
            .or_else(|_| std::env::var("LC_ALL"))
            .unwrap_or_default()
            .to_lowercase();
        if lang.starts_with("ko") {
            Language::Ko
        } else {
            Language::En
        }
    }
}

/// Localized title and description for the permission categories the catalog names in
/// Korean only. Other categories fall back to the catalog label with no description.
pub fn category_text(category: &str, lang: Language) -> Option<(&'static str, &'static str)> {
    let (ko, en) = match category {
        "system" => (
            ("장비 · OS · 사용자 상태", "호스트네임, 리소스(CPU/RAM), 사용자 세션 및 환경 변수 조회"),
            ("Device, OS & User State", "Query hostname, resource metrics (CPU/RAM), active sessions, and environment variables"),
        ),
        "files_read" => (
            ("파일 보기 & 탐색", "지정된 네임드 작업 폴더 내 디렉터리 목록, 이름 검색, 파일 읽기"),
            ("File Inspection & Discovery", "List directories, search filenames, and read file contents in named workspaces"),
        ),
        "files_write" => (
            ("파일 쓰기 & 수정", "신규 파일 생성, 덮어쓰기, 아토믹 파일 쓰기, 삭제 (고위험 제어)"),
            ("File Writing & Mutation", "Create files, overwrite, atomic writes, and delete targets (high-risk control)"),
        ),
        "terminal" => (
            ("터미널 & 명령 실행", "셸 세션 열기, 표준 입출력 파이프 스트리밍, 자식 프로세스 감시"),
            ("Terminal & Command Execution", "Open shell sessions, stream standard I/O pipes, monitor child processes"),
        ),
        "analysis" => (
            ("분석 & 메모리 검색", "정적 파일 구조 파싱, 패턴 검색, 프로세스 메모리 덤프 검사"),
            ("Analysis & Memory Inspection", "Static binary structure parsing, pattern extraction, process memory dump inspection"),
        ),
        "browser" => (
            ("브라우저 & 웹 런타임", "격리된 Chromium 인스턴스 실행 및 웹 제어"),
            ("Browser & Web Runtime", "Launch isolated Chromium instances and automate web interactions"),
        ),
        _ => return None,
    };
    Some(match lang {
        Language::Ko => ko,
        Language::En => en,
    })
}
