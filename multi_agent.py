import os
import json
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
from playwright.async_api import async_playwright, Page, Browser
from openai import OpenAI
from pathlib import Path
from dotenv import load_dotenv


APP_URLS = {
    "linear": "https://linear.app",
    "github": "https://github.com"
}


@dataclass
class UIState:
    """Represents a captured UI state"""
    step_number: int
    description: str
    screenshot_path: str
    url: str
    timestamp: str
    actions_taken: List[str]
    dom_snapshot: Optional[str] = None


@dataclass
class TaskWorkflow:
    """Complete workflow for a task"""
    task_description: str
    app_name: str
    app_url: str
    states: List[UIState]
    total_steps: int
    completion_status: str


class FormFieldTracker:
    """Tracks which fields have been filled to avoid repetition"""
    
    def __init__(self):
        self.filled_fields = {}
        self.field_attempts = {}
        self.filled_positions = set()
        self.content_created = False
        self.choice_made = False
        self.in_creation_flow = False
    
    def create_field_key(self, field_id: str, purpose: str, position_y: int = 0) -> str:
        """Create unique field key using ID, purpose, and position"""
        return f"{field_id}|{purpose}|{position_y//100}"
    
    def mark_filled(self, field_id: str, purpose: str, value: str, position_y: int = 0):
        """Mark a field as filled with position tracking"""
        field_key = self.create_field_key(field_id, purpose, position_y)
        self.filled_fields[field_key] = value
        self.field_attempts[field_key] = self.field_attempts.get(field_key, 0) + 1
        self.filled_positions.add(position_y // 100)
        print(f"    Marked filled: {field_key}")
    
    def is_filled(self, field_id: str, purpose: str, position_y: int = 0) -> bool:
        """Check if field was already filled"""
        field_key = self.create_field_key(field_id, purpose, position_y)
        result = field_key in self.filled_fields
        print(f"    Checking if filled: {field_key} → {result}")
        return result
    
    def get_attempts(self, field_id: str, purpose: str, position_y: int = 0) -> int:
        """Get number of attempts to fill this field"""
        field_key = self.create_field_key(field_id, purpose, position_y)
        return self.field_attempts.get(field_key, 0)
    
    def reset(self):
        """Reset tracker"""
        print("    Tracker reset - all fields cleared")
        self.filled_fields.clear()
        self.field_attempts.clear()
        self.filled_positions.clear()
        self.content_created = False
        self.choice_made = False
        self.in_creation_flow = False


class UniversalUIAgent:
    """Universal UI Navigator - Works with Linear AND GitHub"""
    
    def __init__(self, screenshots_dir: str = "./screenshots"):
        load_dotenv()
        
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if not openai_api_key:
            raise ValueError("OPENAI_API_KEY not found in .env file")
        
        self.client = OpenAI(api_key=openai_api_key)
        self.screenshots_dir = Path(screenshots_dir)
        self.screenshots_dir.mkdir(exist_ok=True)
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.field_tracker = FormFieldTracker()
        
        print("Agent initialized")

        
    async def initialize_browser(self, headless: bool = False):
        """Initialize Playwright browser"""
        playwright = await async_playwright().start()
        
        user_data_dir = "./browser_data"
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir,
            headless=headless,
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            args=['--disable-blink-features=AutomationControlled']
        )
        
        self.browser = context
        self.page = context.pages[0] if context.pages else await context.new_page()
        print("✅ Browser initialized")
        
    async def close_browser(self):
        """Close browser"""
        if self.browser:
            await self.browser.close()
            print("✅ Browser closed")
    
    async def wait_for_login(self, app_name: str, timeout: int = 300) -> bool:
        """Wait for user to login"""
        print(f"\n{'='*60}")
        print("LOGIN REQUIRED - Please login in the browser")
        print(f"{'='*60}\n")
        
        login_keywords = ['login', 'signin', 'auth', 'oauth', 'sessions/verified']
        start_time = datetime.now()
        
        while (datetime.now() - start_time).seconds < timeout:
            current_url = self.page.url.lower()
            is_login = any(kw in current_url for kw in login_keywords)
            
            if not is_login:
                print(f"✅ Login successful: {self.page.url}")
                await self.page.wait_for_timeout(3000)
                return True
            
            await self.page.wait_for_timeout(2000)
        
        return False
    
    async def capture_screenshot(self, step_num: int, description: str) -> str:
        """Capture screenshot"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"step_{step_num}_{timestamp}.png"
        filepath = self.screenshots_dir / filename
        await self.page.screenshot(path=str(filepath), full_page=False)
        return str(filepath)
    
    async def get_comprehensive_dom_context(self) -> Dict[str, Any]:
        """Extract comprehensive DOM context - UNIVERSAL for any app"""
        dom_script = """
        () => {
            const getVisibleElements = () => {
                const selectors = [
                    'button', 'a', 'input', 'select', 'textarea',
                    '[role="button"]', '[role="link"]', '[role="menuitem"]',
                    '[role="textbox"]', '[contenteditable="true"]',
                    '[role="combobox"]', '[role="listbox"]', '[role="option"]',
                    'label', 'form', '[data-testid]', '[aria-label]',
                    'nav a', '[role="navigation"] a', '[role="tab"]',
                    '[placeholder]', '[name]', '[type="submit"]',
                    'summary', 'details', '[data-menu-button]',
                    '.btn', '.Button', '[class*="button"]', '[class*="Button"]'
                ];
                
                const elements = [];
                const seenElements = new Set();
                
                const classifyElement = (el) => {
                    const text = (el.textContent || '').trim().toLowerCase();
                    const ariaLabel = (el.getAttribute('aria-label') || '').toLowerCase();
                    const placeholder = (el.getAttribute('placeholder') || '').toLowerCase();
                    const href = (el.href || '').toLowerCase();
                    const className = (el.className?.toString() || '').toLowerCase();
                    const dataTarget = (el.getAttribute('data-target') || '').toLowerCase();
                    const combined = text + ' ' + ariaLabel + ' ' + href + ' ' + placeholder + ' ' + className + ' ' + dataTarget;
                    
                    // Navigation keywords - GitHub specific
                    const navKeywords = ['repositories', 'issues', 'pull requests', 'projects', 
                                        'discussions', 'actions', 'packages', 'security',
                                        'insights', 'settings', 'code', 'commits', 'branches',
                                        'wiki', 'marketplace', 'explore', 'topics', 'trending',
                                        'your repositories', 'your projects', 'your organizations',
                                        'dashboard', 'profile', 'stars', 'gists'];
                    
                    // Creation/Action keywords - GitHub specific
                    const createKeywords = ['new repository', 'new issue', 
                                          'new pull request', 'new discussion', 'create repository','add project',
                                          'create project', 'create issue', 'new gist', 'import repository',
                                          'new organization', 'create', 'add file', 'upload files',
                                          'create new file', '+', 'new'];
                    
                    // Template/option selection
                    const templateKeywords = ['template', 'use this template', 'choose a template',
                                            'start from scratch', 'blank', 'table', 'board', 
                                            'roadmap', 'kanban'];
                    
                    // Visibility/privacy options
                    const visibilityKeywords = ['public', 'private', 'internal', 'visibility'];
                    
                    // Intermediate action keywords
                    const intermediateKeywords = ['continue', 'next', 'proceed', 'skip', 'add', 
                                                'choose', 'select', 'import'];
                    
                    // Final submit keywords
                    const finalSubmitKeywords = ['create repository', 'create project', 'create issue',
                                               'submit new issue', 'create pull request', 'publish',
                                                'commit changes', 'propose changes'];
                    
                    // Repository settings
                    const repoSettingsKeywords = ['readme', 'gitignore', 'license', 'initialize',
                                                'add readme', 'add gitignore', 'add license'];
                    
                    const cancelKeywords = ['cancel', 'close', 'dismiss', 'back', 'discard'];
                    
                    // Classify element
                    const isNav = navKeywords.some(kw => combined.includes(kw)) && 
                                  (el.tagName === 'A' || el.getAttribute('role') === 'link' || 
                                   el.getAttribute('role') === 'tab' || el.closest('[role="navigation"]'));
                    
                    const isCreate = createKeywords.some(kw => combined.includes(kw)) && 
                                   !cancelKeywords.some(kw => combined.includes(kw));
                    
                    const isTemplate = templateKeywords.some(kw => combined.includes(kw));
                    const isVisibility = visibilityKeywords.some(kw => combined.includes(kw));
                    const isRepoSetting = repoSettingsKeywords.some(kw => combined.includes(kw));
                    const isIntermediate = intermediateKeywords.some(kw => combined.includes(kw)) &&
                                         !finalSubmitKeywords.some(kw => combined.includes(kw)) &&
                                         !cancelKeywords.some(kw => combined.includes(kw));
                    const isFinalSubmit = finalSubmitKeywords.some(kw => combined.includes(kw)) &&
                                        !intermediateKeywords.some(kw => combined.includes(kw)) &&
                                        !cancelKeywords.some(kw => combined.includes(kw));
                    const isCancel = cancelKeywords.some(kw => combined.includes(kw));
                    
                    let purpose = 'other';
                    if (isNav) purpose = 'navigation';
                    else if (isCreate && !isCancel) purpose = 'create';
                    else if (isTemplate && !isCancel) purpose = 'template_choice';
                    else if (isVisibility && !isCancel) purpose = 'visibility_choice';
                    else if (isRepoSetting && !isCancel) purpose = 'repo_setting';
                    else if (isIntermediate && !isCancel) purpose = 'intermediate';
                    else if (isFinalSubmit && !isCancel) purpose = 'final_submit';
                    else if (isCancel) purpose = 'cancel';
                    
                    return purpose;
                };
                
                selectors.forEach(sel => {
                    try {
                        document.querySelectorAll(sel).forEach(el => {
                            if (seenElements.has(el)) return;
                            
                            if (el.offsetParent !== null || el.checkVisibility?.()) {
                                const rect = el.getBoundingClientRect();
                                if (rect.top < window.innerHeight && rect.bottom > 0 && 
                                    rect.width > 0 && rect.height > 0) {
                                    
                                    const text = (el.textContent || '').trim();
                                    const innerText = (el.innerText || '').trim();
                                    
                                    const fieldId = [
                                        el.getAttribute('aria-label'),
                                        el.getAttribute('placeholder'),
                                        el.getAttribute('aria-placeholder'),
                                        el.getAttribute('name'),
                                        el.getAttribute('id'),
                                        el.getAttribute('data-testid'),
                                        el.getAttribute('data-target'),
                                        el.closest('label')?.textContent?.trim().substring(0, 30)
                                    ].filter(x => x).join('|') || text.substring(0, 30);
                                    
                                    const isButton = el.tagName === 'BUTTON' || 
                                                    el.getAttribute('role') === 'button' ||
                                                    (el.tagName === 'A' && el.href && el.href !== '#') ||
                                                    (el.tagName === 'SUMMARY') ||
                                                    el.classList.contains('btn') ||
                                                    el.classList.contains('Button');
                                    
                                    const elementPurpose = classifyElement(el);
                                    
                                    elements.push({
                                        tag: el.tagName.toLowerCase(),
                                        text: text.substring(0, 150),
                                        innerText: innerText.substring(0, 150),
                                        type: el.type || el.getAttribute('role') || '',
                                        placeholder: el.placeholder || el.getAttribute('placeholder') || '',
                                        aria_label: el.getAttribute('aria-label') || '',
                                        aria_placeholder: el.getAttribute('aria-placeholder') || '',
                                        name: el.name || '',
                                        id: el.id || '',
                                        href: el.href || '',
                                        className: el.className?.toString().substring(0, 150) || '',
                                        value: el.value || (el.textContent || '').trim().substring(0, 100),
                                        required: el.required || false,
                                        contentEditable: el.contentEditable === 'true',
                                        testId: el.getAttribute('data-testid') || '',
                                        dataTarget: el.getAttribute('data-target') || '',
                                        fieldId: fieldId,
                                        elementPurpose: elementPurpose,
                                        position: {
                                            x: Math.round(rect.x),
                                            y: Math.round(rect.y),
                                            width: Math.round(rect.width),
                                            height: Math.round(rect.height)
                                        },
                                        isVisible: true,
                                        isInput: ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) || 
                                                 el.contentEditable === 'true' ||
                                                 el.getAttribute('role') === 'textbox',
                                        isButton: isButton,
                                        isNavigation: elementPurpose === 'navigation',
                                        isCreateButton: elementPurpose === 'create',
                                        isTemplateChoice: elementPurpose === 'template_choice',
                                        isVisibilityChoice: elementPurpose === 'visibility_choice',
                                        isRepoSetting: elementPurpose === 'repo_setting',
                                        isContentEditable: el.contentEditable === 'true',
                                        disabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
                                        hasValue: !!(el.value || (el.contentEditable === 'true' && el.textContent?.trim()))
                                    });
                                    
                                    seenElements.add(el);
                                }
                            }
                        });
                    } catch (e) {
                        console.error('Error processing selector:', sel, e);
                    }
                });
                
                return elements.slice(0, 400);
            };
            
            const detectDialogs = () => {
                const dialogs = [];
                document.querySelectorAll('[role="dialog"], [role="modal"], .modal, .dialog, [class*="Modal"], [class*="Dialog"]').forEach(el => {
                    if (el.offsetParent !== null) {
                        const rect = el.getBoundingClientRect();
                        dialogs.push({
                            text: el.textContent.substring(0, 500),
                            position: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
                            aria_label: el.getAttribute('aria-label') || '',
                            isVisible: true
                        });
                    }
                });
                return dialogs;
            };
            
            const detectCurrentSection = () => {
                const url = window.location.href.toLowerCase();
                const pathname = window.location.pathname.toLowerCase();
                
                if (url.includes('github.com')) {
                    if (pathname.includes('/new')) return 'creating_new';
                    if (pathname.includes('/issues')) return 'issues';
                    if (pathname.includes('/pull')) return 'pull_requests';
                    if (pathname.includes('/projects')) return 'projects';
                    if (pathname.includes('/settings')) return 'settings';
                    if (pathname.includes('/actions')) return 'actions';
                    if (pathname === '/' || pathname === '') return 'dashboard';
                    if (pathname.split('/').length === 3) return 'repository';
                    return 'github_main';
                }
                
                if (url.includes('linear.app')) {
                    if (url.includes('/project')) return 'projects';
                    if (url.includes('/issue')) return 'issues';
                    if (url.includes('/settings')) return 'settings';
                    if (url.includes('/team')) return 'team';
                    return 'linear_main';
                }
                
                return 'unknown';
            };
            
            const isGitHub = window.location.href.includes('github.com');
            
            return {
                url: window.location.href,
                title: document.title,
                currentSection: detectCurrentSection(),
                elements: getVisibleElements(),
                dialogs: detectDialogs(),
                hasDialog: document.querySelectorAll('[role="dialog"], [role="modal"]').length > 0,
                focusedElement: document.activeElement?.tagName?.toLowerCase() || 'none',
                isGitHub: isGitHub,
                isLinear: window.location.href.includes('linear.app')
            };
        }
        """
        
        try:
            dom_data = await self.page.evaluate(dom_script)
            return dom_data
        except Exception as e:
            print(f"DOM extraction error: {e}")
            return {"url": self.page.url, "title": "Error", "currentSection": "unknown", 
                    "elements": [], "dialogs": [], "isGitHub": False, "isLinear": False}
    
    def create_smart_prompt(
        self,
        task: str,
        app_name: str,
        current_step: int,
        dom_data: Dict[str, Any],
        previous_actions: List[str],
        consecutive_failures: int
    ) -> str:
        """Create intelligent prompt - UNIVERSAL, learns from UI"""
        
        task_lower = task.lower()
        task_entity = None
        if 'repository' in task_lower or 'repo' in task_lower:
            task_entity = 'repository'
        elif 'project' in task_lower:
            task_entity = 'project'
        elif 'issue' in task_lower:
            task_entity = 'issue'
        elif 'pull request' in task_lower or 'pr' in task_lower:
            task_entity = 'pull_request'
        elif 'discussion' in task_lower:
            task_entity = 'discussion'
        elif 'gist' in task_lower:
            task_entity = 'gist'
        
        # Get filled fields info
        filled_info = []
        if self.field_tracker.filled_fields:
            for field_key, value in list(self.field_tracker.filled_fields.items())[:10]:
                parts = field_key.split('|')
                display_key = '|'.join(parts[:2]) if len(parts) >= 2 else field_key
                filled_info.append(f"  - '{display_key[:40]}' = '{value[:40]}'")
        filled_summary = "\n".join(filled_info) if filled_info else "  None"
        
        # Analyze DOM
        current_section = dom_data.get('currentSection', 'unknown')
        has_dialog = dom_data.get('hasDialog', False) or len(dom_data.get('dialogs', [])) > 0
        is_github = dom_data.get('isGitHub', False)
        is_linear = dom_data.get('isLinear', False)
        elements = dom_data.get('elements', [])
        
        input_fields = [el for el in elements if el.get('isInput')]
        contenteditable_fields = [el for el in elements if el.get('isContentEditable')]
        navigation_elements = [el for el in elements if el.get('isNavigation')]
        create_buttons = [el for el in elements if el.get('isCreateButton')]
        template_choices = [el for el in elements if el.get('isTemplateChoice')]
        visibility_choices = [el for el in elements if el.get('isVisibilityChoice')]
        repo_settings = [el for el in elements if el.get('isRepoSetting')]
        intermediate_buttons = [el for el in elements if el.get('elementPurpose') == 'intermediate']
        submit_buttons = [el for el in elements if el.get('elementPurpose') == 'final_submit' and not el.get('disabled')]
        
        # Build navigation section
        nav_section = "NAVIGATION ELEMENTS:\n"
        if navigation_elements:
            for i, nav in enumerate(navigation_elements[:15], 1):
                nav_text = nav.get('text', '')[:50] or nav.get('aria_label', '')[:50]
                if nav_text:
                    nav_section += f"{i}. '{nav_text}'\n"
        else:
            nav_section += "  No navigation elements found\n"
        
        # Build create buttons section
        create_section = "🆕 CREATE/NEW BUTTONS:\n"
        if create_buttons:
            for i, btn in enumerate(create_buttons[:10], 1):
                btn_text = btn.get('text', '')[:50] or btn.get('aria_label', '')[:50]
                btn_disabled = "DISABLED" if btn.get('disabled') else "ENABLED"
                create_section += f"{i}. {btn_disabled} | '{btn_text}'\n"
        else:
            create_section += "  No create buttons found\n"
        
        # Build template/option choices
        choice_section = "TEMPLATE/OPTION CHOICES:\n"
        if template_choices:
            choice_section += "  TEMPLATE OPTIONS:\n"
            for i, btn in enumerate(template_choices[:10], 1):
                btn_text = btn.get('text', '')[:80] or btn.get('aria_label', '')[:80]
                btn_disabled = "DISABLED" if btn.get('disabled') else "ENABLED"
                choice_section += f"    {i}. {btn_disabled} | '{btn_text}'\n"
        if not template_choices:
            choice_section += "  No template choices visible\n"
        
        # Build visibility choices
        visibility_section = "VISIBILITY OPTIONS (Public/Private):\n"
        if visibility_choices:
            for i, btn in enumerate(visibility_choices[:5], 1):
                btn_text = btn.get('text', '')[:50] or btn.get('aria_label', '')[:50]
                btn_disabled = "DISABLED" if btn.get('disabled') else "ENABLED"
                visibility_section += f"{i}. {btn_disabled} | '{btn_text}'\n"
        else:
            visibility_section += "  No visibility options visible\n"
        
        # Build repo settings
        settings_section = "REPOSITORY SETTINGS (README/gitignore/License):\n"
        if repo_settings:
            for i, setting in enumerate(repo_settings[:10], 1):
                text = setting.get('text', '')[:50] or setting.get('aria_label', '')[:50]
                settings_section += f"{i}. '{text}'\n"
        else:
            settings_section += "  No repo settings visible\n"
        
        # Build intermediate buttons section
        intermediate_section = "INTERMEDIATE BUTTONS (Continue/Next/Add):\n"
        if intermediate_buttons:
            for i, btn in enumerate(intermediate_buttons[:10], 1):
                btn_text = btn.get('text', '')[:50] or btn.get('aria_label', '')[:50]
                btn_disabled = "DISABLED" if btn.get('disabled') else "ENABLED"
                intermediate_section += f"{i}. {btn_disabled} | '{btn_text}'\n"
        else:
            intermediate_section += "  No intermediate buttons found\n"
        
        # Build submit buttons section
        submit_section = "✅ FINAL SUBMIT BUTTONS (Create/Save/Publish):\n"
        if submit_buttons:
            submit_section += "  USE EXACT FULL TEXT - Don't use partial words!\n"
            for i, btn in enumerate(submit_buttons[:10], 1):
                btn_text = btn.get('text', '')[:50] or btn.get('aria_label', '')[:50]
                btn_disabled = "DISABLED" if btn.get('disabled') else "ENABLED"
                submit_section += f"{i}. {btn_disabled} | EXACT TEXT: '{btn_text}'\n"
        else:
            submit_section += "  No final submit buttons found (may need intermediate step first)\n"
        
        # Build field analysis
        field_analysis = "📝 INPUT FIELDS:\n"
        
        # Show contenteditable if any
        if contenteditable_fields:
            field_analysis += "\nCONTENTEDITABLE FIELDS:\n"
            for i, field in enumerate(contenteditable_fields[:10], 1):
                field_id = field.get('fieldId', 'unknown')[:50]
                text = field.get('text', '')[:30]
                placeholder = field.get('placeholder', '')[:30]
                aria = field.get('aria_label', '')[:30]
                pos_y = field.get('position', {}).get('y', 0)
                status = "HAS CONTENT" if text else "EMPTY"
                
                identifiers = []
                if placeholder: identifiers.append(f"placeholder='{placeholder}'")
                if aria: identifiers.append(f"aria='{aria}'")
                
                identifier_str = " | ".join(identifiers) if identifiers else field_id
                field_analysis += f"{i}. {status} | Y:{pos_y} | {identifier_str}\n"
        
        # Show traditional inputs
        traditional_inputs = [f for f in input_fields if not f.get('isContentEditable')]
        if traditional_inputs:
            field_analysis += "\nINPUT FIELDS:\n"
            for i, field in enumerate(traditional_inputs[:15], 1):
                field_id = field.get('fieldId', 'unknown')[:50]
                field_name = field.get('name', '')
                field_element_id = field.get('id', '')
                placeholder = field.get('placeholder', '')[:40]
                aria = field.get('aria_label', '')[:40]
                pos_y = field.get('position', {}).get('y', 0)
                status = "FILLED" if field.get('hasValue') else "EMPTY"
                
                combined_text = (placeholder + ' ' + aria + ' ' + field_id).lower()
                field_purpose = 'other'
                if any(kw in combined_text for kw in ['name', 'title', 'repository name', 'project name']):
                    field_purpose = 'name'
                elif any(kw in combined_text for kw in ['description', 'summary', 'about']):
                    field_purpose = 'description'
                
                try:
                    is_already_filled = self.field_tracker.is_filled(field_id, field_purpose, pos_y)
                    already_filled = "SKIP" if is_already_filled else "CAN FILL"
                except:
                    already_filled = "CAN FILL"
                
                identifiers = []
                if placeholder: identifiers.append(f"placeholder='{placeholder}'")
                if aria: identifiers.append(f"aria='{aria}'")
                if field_element_id: identifiers.append(f"id='{field_element_id}'")
                if field_name: identifiers.append(f"name='{field_name}'")
                
                identifier_str = " | ".join(identifiers) if identifiers else field_id
                field_analysis += f"{i}. {status} | {already_filled} | Y:{pos_y} | {identifier_str}\n"
        
        task_context = f"""
    🎯 TASK CONTEXT ANALYSIS:
    - Original Task: "{task}"
    - Target Entity: {task_entity or 'general'}
    - Current App: {'GITHUB' if is_github else 'LINEAR' if is_linear else 'UNKNOWN'}
    - Current Section: {current_section}
    - Has Dialog Open: {has_dialog}
    - Choice Made: {self.field_tracker.choice_made}
    - In Creation Flow: {self.field_tracker.in_creation_flow}
    - Content Already Created: {self.field_tracker.content_created}
    """
        
        # GITHUB-specific smart workflow
        github_workflow = ""
        if is_github:
            github_workflow = f"""
    GITHUB WORKFLOW - FULLY DYNAMIC (NO HARDCODING)

    CURRENT STATE ANALYSIS:
    - Task Entity: {task_entity or 'unknown'}
    - Current Section: {current_section}
    - Dialog Open: {has_dialog}
    - Choice Made: {self.field_tracker.choice_made}
    - In Flow: {self.field_tracker.in_creation_flow}

    SMART WORKFLOW - LEARN FROM UI ELEMENTS:

    STEP 1️ - NAVIGATE TO CREATION:
    Current section: {current_section}
    
    Common GitHub creation patterns:
    - Repositories: Click "+" dropdown → "New repository" OR navigate to /new
    - Projects: Go to /projects → Click "New project"
    - Issues: In repository → Click "Issues" tab → "New issue"
    - Pull Requests: In repository → Click "Pull requests" → "New pull request"
    
    Look at "CREATE/NEW BUTTONS" section:
    - Find button matching your task entity ({task_entity})
    - Common texts: "New repository", "New project", "New issue", "Create repository"
    - Click to start creation flow
    - After click → mark in_creation_flow = True

    STEP 2️ - FILL REQUIRED FIELDS (Name/Title):
    Check "INPUT FIELDS" section above
    
    Repository creation typically needs:
    - Repository name (REQUIRED)
    - Description (optional)
    
    Project creation typically needs:
    - Project name (REQUIRED)
    - Description (optional)
    
    Issue creation typically needs:
    - Title (REQUIRED)
    - Description/Body (optional)
    
    Rules:
    - Fill fields marked "CAN FILL"
    - Skip fields marked "SKIP"
    - Use Y-position to differentiate similar fields
    - Fill in order: name/title first, then description

    STEP 3️ - MAKE CHOICES (if presented):
    
    A) Repository Visibility (if creating repo):
    Check " VISIBILITY OPTIONS" section
    - Look for "Public" or "Private" radio buttons/options
    - Default is usually "Public" (already selected)
    - Click if you need to change it
    
    B) Repository Settings (optional):
    Check "REPOSITORY SETTINGS" section
    - Add README file (checkbox)
    - Add .gitignore (dropdown/checkbox)
    - Choose a license (dropdown)
    - These are OPTIONAL - GitHub can create repo without them
    
    C) Project Template (if creating project):
    Check " TEMPLATE/OPTION CHOICES" section
    - Look for: "Table", "Board", "Roadmap", "Blank"
    - Click desired template
    - Mark choice_made = True after selection

    STEP 4️ - INTERMEDIATE STEPS:
    Check " INTERMEDIATE BUTTONS" section
    - Look for: "Continue", "Next", "Add", "Import"
    - These advance through multi-step flows
    - Click if present and enabled

    STEP 5️ - FINAL SUBMIT:
    Check "FINAL SUBMIT BUTTONS" section
    - Look for: "Create repository", "Create project", "Create issue"
    - This is the FINAL action - completes the creation
    - Must have exact text match (don't click partial matches)
    - Button should be ENABLED (✅)

    STEP 6 - VERIFY COMPLETION:
    After clicking final submit:
    - Check if URL changed to new entity (e.g., /username/repo-name)
    - Check if dialog closed
    - Check if success message visible
    - If yes → set is_complete=true, confidence > 0.8

    CRITICAL DYNAMIC RULES:
    1. DON'T assume button/field names - READ from UI sections above
    2. DON'T hardcode selectors - use text/aria-label from elements
    3. DO adapt to what's visible NOW on the page
    4. DO track state with choice_made and in_creation_flow
    5. DON'T confuse intermediate buttons with final submit
    6. DO use exact text from "✅FINAL SUBMIT BUTTONS" section
    7. DO check if buttons are ENABLED before clicking
    8. DON'T fill fields marked 🔒 SKIP (already filled)
    9. DO use Y-position to differentiate duplicate field names

    🎯 DECISION TREE FOR NEXT ACTION:

    1️ If NOT in creation section yet:
    → Navigate using "🧭 NAVIGATION" or "CREATE" buttons
    → Example: Click "+", then "New repository"

    2️ If in creation flow but required fields empty:
    → Fill name/title field first (REQUIRED)
    → Then fill description if needed (optional)

    3️ If required fields filled AND choices visible (visibility/template):
    → Make choice if not default
    → Mark choice_made = True

    4️ If everything filled AND intermediate button visible:
    → Click "Continue" or "Next"
    → Advance to next step

    5️ If everything ready AND final submit button ENABLED:
    → Click "Create repository" / "Create project" / etc.
    → This completes the task

    6️ If entity visible in new URL or UI:
    → Mark complete

    WHAT I SEE RIGHT NOW:
    - Create buttons: {len(create_buttons)} visible
    - Name fields: {len([f for f in input_fields if 'name' in f.get('fieldId', '').lower() or 'title' in f.get('fieldId', '').lower()])} visible
    - Visibility options: {len(visibility_choices)} visible
    - Repo settings: {len(repo_settings)} visible
    - Template choices: {len(template_choices)} visible
    - Intermediate buttons: {len(intermediate_buttons)} visible
    - Final submit buttons: {len(submit_buttons)} visible

    GITHUB-SPECIFIC TIPS:
    - Repository name must be unique in your account
    - Repository names can contain letters, numbers, hyphens, underscores
    - Public repos are visible to everyone, Private repos need permission
    - README/gitignore/license are optional - can be added later
    - Projects can be created at user or org level
    - Issues/PRs require being in a repository first

    COMMON GITHUB PATTERNS:
    - "+" button in top-right → Opens dropdown with "New repository", "Import repository", "New gist"
    - Repository page → "Issues" tab → "New issue" button
    - Repository page → "Pull requests" tab → "New pull request" button
    - User profile → "Projects" tab → "New project" button
    - Organization page → "New repository" button prominent
    """
        
        # Linear-specific workflow (UNCHANGED)
        linear_workflow = ""
        if is_linear:
            linear_workflow = f"""
    LINEAR-SPECIFIC WORKFLOW (UNCHANGED):

    PHASE 1 - NAVIGATION:
    - If task mentions PROJECT but section ≠ 'projects' → Navigate
    - If task mentions ISSUE → Look for "New Issue" button

    PHASE 2 - OPEN FORM:
    - Click "New Project", "Create Issue", etc.

    PHASE 3 - FILL FIELDS:
    - Fill EMPTY fields with 🆕 CAN FILL status
    - Skip fields showing SKIP
    - Use Y-position to differentiate

    PHASE 4 - SUBMIT:
    - Click submit button from "✅ FINAL SUBMIT BUTTONS"
 CRITICAL FOR LINEAR:
- The SUBMIT button text is just "Create issue" (NOT "Create new issue")
- "Create new issue" = Opens the form (already done)
- "Create issue" = Submits the form (final action)
- Look for exact text "Create issue" in submit buttons section
- Click "Create issue" to complete task
    """
        
        prompt = f"""You are a universal web UI automation agent that learns from the UI.

    TASK: "{task}"

    {task_context}

    CURRENT STATE:
    - Step: {current_step}
    - Previous Actions: {json.dumps(previous_actions[-5:]) if previous_actions else "None"}
    - Consecutive Failures: {consecutive_failures}

    FIELDS ALREADY FILLED:
    {filled_summary}

    {nav_section}

    {create_section}

    {choice_section}

    {visibility_section}

    {settings_section}

    {intermediate_section}

    {submit_section}

    {field_analysis}

    {github_workflow if is_github else linear_workflow if is_linear else ""}

    UNIVERSAL CRITICAL RULES:
    1. LEARN from UI - don't assume element locations
    2. DON'T fill fields marked SKIP
    3. DO use Y-position to differentiate fields
    4. DO provide target_y_position in response
    5. For GitHub: 
       - Read ALL available buttons from sections above
       - Fill required fields first (name/title)
       - Make visibility/template choices if needed
       - Follow intermediate steps if present
       - Final submit only when everything is ready
       - Use EXACT text from submit buttons section
    6. For Linear: Use existing logic (DON'T CHANGE)
    7. Be adaptive - if one approach fails, try alternatives from available elements
    8. ALWAYS check if button is ENABLED before clicking (disabled buttons won't work)
    9. If submit_buttons visible AND required fields filled → Click submit to complete!

    RESPONSE FORMAT (JSON only):
    {{
        "reasoning": "explain what you see, current state, and why you chose this action",
        "action": "click" | "type" | "type_contenteditable" | "press_key" | "navigate" | "wait" | "complete",
        "target": "exact text or identifier from UI sections above",
        "selector_type": "text" | "placeholder" | "aria_label" | "contenteditable" | "id" | "name",
        "value": "text to type (if typing)",
        "field_purpose": "title|description|name|summary|body|other",
        "target_y_position": 0,
        "wait_after": 2000,
        "is_complete": false,
        "confidence": 0.9
    }}

    CRITICAL FOR TYPING:
    - Use BEST identifier: id > name > placeholder > aria-label  
    - Set selector_type to match what you're using
    - Always include accurate target_y_position from field info above

    PAGE DATA:
    {json.dumps(dom_data, indent=2)[:12000]}"""

        return prompt
    
    async def analyze_and_plan(
        self, 
        task: str,
        app_name: str,
        current_step: int,
        dom_data: Dict[str, Any],
        previous_actions: List[str],
        consecutive_failures: int = 0
    ) -> Dict[str, Any]:
        """Use GPT-4 to intelligently plan next action"""
        
        prompt = self.create_smart_prompt(
            task, app_name, current_step, dom_data, 
            previous_actions, consecutive_failures
        )
        
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1500
            )
            
            response_text = response.choices[0].message.content.strip()
            
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            
            if json_start != -1 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                action_plan = json.loads(json_str)
                
                if 'action' not in action_plan:
                    action_plan['action'] = 'wait'
                
                action_plan.setdefault('wait_after', 1500)
                action_plan.setdefault('confidence', 0.5)
                action_plan.setdefault('is_complete', False)
                action_plan.setdefault('target_y_position', 0)
                
                return action_plan
            else:
                return {
                    "action": "wait",
                    "reasoning": "Could not parse LLM response",
                    "is_complete": False,
                    "wait_after": 2000,
                    "confidence": 0.3,
                    "target_y_position": 0
                }
                
        except Exception as e:
            print(f"❌ GPT-4 error: {e}")
            return {
                "action": "wait",
                "reasoning": f"Error: {str(e)}",
                "is_complete": False,
                "wait_after": 2000,
                "confidence": 0.1,
                "target_y_position": 0
            }
    
    async def execute_action(self, action_plan: Dict[str, Any]) -> Tuple[bool, str]:
        """Execute planned action"""
        action = action_plan['action']
        target = action_plan.get('target', '').strip()
        
        try:
            if action == 'complete':
                return True, "Task marked as complete"
                
            elif action == 'press_key':
                key = target or action_plan.get('value', 'Enter')
                print(f"Pressing key: '{key}'")
                
                try:
                    await self.page.keyboard.press(key)
                    await self.page.wait_for_timeout(800)
                    print(f"  Pressed key: {key}")
                    return True, f"Pressed key: {key}"
                except Exception as e:
                    print(f"  Key press failed: {e}")
                    return False, f"Key press error: {str(e)}"
                
            elif action in ['click', 'navigate']:
                print(f"Clicking: '{target}'")
                success, msg = await self._smart_click(target, action_plan.get('selector_type', 'text'))
                
                # Reset tracker when opening new form/page
                if success and any(word in target.lower() for word in ['create', 'new', 'add', 'repository', 'project', 'issue']):
                    self.field_tracker.reset()
                
                return success, msg
                
            elif action == 'type_contenteditable':
                value = action_plan.get('value', '')
                field_purpose = action_plan.get('field_purpose', 'other')
                target_y = action_plan.get('target_y_position', 0)
                
                field_id = target
                
                print(f"Typing into contenteditable '{target}' at Y:{target_y} (purpose: {field_purpose})")
                
                if self.field_tracker.is_filled(field_id, field_purpose, target_y):
                    print(f"  Contenteditable already filled, skipping...")
                    return True, f"Skipped (already filled)"
                
                if self.field_tracker.get_attempts(field_id, field_purpose, target_y) >= 2:
                    print(f"  Too many attempts, skipping...")
                    return True, f"Skipped (too many attempts)"
                
                success, msg = await self._type_contenteditable(target, value, target_y)
                
                if success:
                    self.field_tracker.mark_filled(field_id, field_purpose, value, target_y)
                    if field_purpose in ['title', 'body', 'content']:
                        self.field_tracker.content_created = True
                
                return success, msg
                
            elif action == 'type':
                value = action_plan.get('value', '')
                field_purpose = action_plan.get('field_purpose', 'other')
                target_y = action_plan.get('target_y_position', 0)
                selector_type = action_plan.get('selector_type', 'placeholder')
                
                field_id = target
                
                print(f"Typing '{value}' into '{target}' at Y:{target_y} (purpose: {field_purpose}, selector: {selector_type})")
                
                if self.field_tracker.is_filled(field_id, field_purpose, target_y):
                    print(f"  Field already filled, skipping...")
                    return True, f"Skipped (already filled)"
                
                if self.field_tracker.get_attempts(field_id, field_purpose, target_y) >= 2:
                    print(f"  Too many attempts, skipping...")
                    return True, f"Skipped (too many attempts)"
                
                success, msg = await self._smart_type(target, value, selector_type, target_y)
                
                if success:
                    self.field_tracker.mark_filled(field_id, field_purpose, value, target_y)
                
                return success, msg
                
            elif action == 'wait':
                wait_time = action_plan.get('wait_after', 2000)
                print(f"Waiting {wait_time}ms...")
                await self.page.wait_for_timeout(wait_time)
                return True, f"Waited {wait_time}ms"
            
            return False, f"Unknown action: {action}"
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return False, f"Error: {str(e)}"
    
    async def _type_contenteditable(self, target: str, value: str, target_y: int = 0) -> Tuple[bool, str]:
        """Type into contenteditable div with position awareness"""
        
        strategies = []
        
        if target:
            strategies.extend([
                f"[contenteditable='true'][placeholder*='{target}' i]",
                f"[contenteditable='true'][aria-label*='{target}' i]",
                f"[contenteditable='true']:has-text('{target}')"
            ])
        
        strategies.extend([
            "[contenteditable='true']:empty",
            "[contenteditable='true']"
        ])
        
        best_match = None
        best_distance = float('inf')
        best_strategy_used = 0
        
        for i, selector in enumerate(strategies, 1):
            try:
                locator = self.page.locator(selector)
                count = await locator.count()
                
                if count > 0:
                    if target_y > 0:
                        for idx in range(min(count, 5)):
                            try:
                                elem = locator.nth(idx)
                                if await elem.is_visible(timeout=1000):
                                    bbox = await elem.bounding_box()
                                    if bbox:
                                        elem_y = int(bbox['y'])
                                        distance = abs(elem_y - target_y)
                                        
                                        if distance < 100 and distance < best_distance:
                                            best_distance = distance
                                            best_match = elem
                                            best_strategy_used = i
                                            
                                            if distance < 15:
                                                break
                            except:
                                continue
                        
                        if best_match and best_strategy_used < len(strategies) - 1:
                            break
                    else:
                        if i < len(strategies) - 1:
                            for idx in range(min(count, 2)):
                                try:
                                    elem = locator.nth(idx)
                                    if await elem.is_visible(timeout=1000):
                                        best_match = elem
                                        best_strategy_used = i
                                        break
                                except:
                                    continue
                        
                        if best_match:
                            break
            except:
                continue
        
        if best_match:
            try:
                await best_match.click(timeout=3000)
                await self.page.wait_for_timeout(500)
                await self.page.keyboard.type(value, delay=50)
                await self.page.wait_for_timeout(800)
                
                print(f"  ✅ Typed into contenteditable using strategy {best_strategy_used} (Y-distance: {best_distance:.0f}px)")
                return True, f"Typed into contenteditable using strategy {best_strategy_used}"
            except Exception as e:
                print(f"  ❌ Contenteditable typing failed: {e}")
        
        return False, f"Could not type into contenteditable: {target}"
    
    async def _smart_click(self, target: str, selector_type: str = 'text') -> Tuple[bool, str]:
        """Smart clicking with fallback strategies"""
        
        strategies = []
        keywords = target.lower().split()
        
        # Check if this is an intermediate action vs final submit
        is_intermediate = any(word in target.lower() for word in 
                            ['continue', 'next', 'proceed', 'choose', 'select', 'skip', 'add'])
        is_final_submit = any(phrase in target.lower() for phrase in 
                            ['create repository', 'create project', 'create issue', 'submit', 'publish', 'save'])
        
        if selector_type == 'text':
            # Strategy 1: For final submit, prioritize multi-word exact matches
            if is_final_submit and ' ' in target:
                strategies.extend([
                    f"button:has-text('{target}'):not([disabled])",
                    f"[role='button']:has-text('{target}'):not([aria-disabled='true'])",
                    f"text=/^{target}$/i",
                    f":text-is('{target}')",
                    f"button:text-is('{target}')",
                    f"button >> text=/{target}/i",
                    f"[type='submit']:has-text('{target}')",
                ])
            else:
                # Strategy 1: Exact text match (highest priority)
                strategies.extend([
                    f"text=/^{target}$/i",
                    f":text-is('{target}')",
                ])
            
            # Strategy 2: Specific element types with exact text
            if is_intermediate:
                strategies.extend([
                    f"button:has-text('{target}'):not([disabled])",
                    f"[role='button']:has-text('{target}'):not([aria-disabled='true'])",
                ])
            
            strategies.extend([
                f"button:has-text('{target}')",
                f"a:has-text('{target}')",
                f"[role='button']:has-text('{target}')",
                f"[role='link']:has-text('{target}')",
                f"summary:has-text('{target}')",  # GitHub dropdowns
            ])
            
            # Strategy 3: Partial text match
            strategies.extend([
                f"text=/{target}/i",
                f":text('{target}')"
            ])
            
            # Strategy 4: Keyword-based matching
            if not is_final_submit or not ' ' in target:
                for word in keywords:
                    if len(word) > 3:
                        strategies.extend([
                            f"button:has-text('{word}'):not([disabled])",
                            f"[role='button']:has-text('{word}'):not([aria-disabled='true'])",
                        ])
        
        elif selector_type == 'placeholder':
            strategies = [
                f"[placeholder='{target}' i]",
                f"[placeholder*='{target}' i]"
            ]
        elif selector_type == 'aria_label':
            strategies = [
                f"[aria-label='{target}' i]",
                f"[aria-label*='{target}' i]"
            ]
        elif selector_type == 'id':
            strategies = [
                f"#{target}",
                f"[id='{target}']"
            ]
        elif selector_type == 'name':
            strategies = [
                f"[name='{target}']",
                f"[name*='{target}' i]"
            ]
        
        for i, strategy in enumerate(strategies, 1):
            try:
                locator = self.page.locator(strategy).first
                if await locator.count() > 0:
                    if is_final_submit and ' ' in target:
                        try:
                            await locator.scroll_into_view_if_needed(timeout=2000)
                            await self.page.wait_for_timeout(500)
                        except:
                            pass
                    
                    if await locator.is_visible(timeout=2000):
                        # Check if button is disabled
                        is_disabled = await locator.evaluate("el => el.disabled || el.getAttribute('aria-disabled') === 'true'")
                        if is_disabled:
                            print(f"  ⚠️ Strategy {i} found disabled element, skipping...")
                            continue
                        
                        # Additional check for intermediate vs final
                        if is_intermediate:
                            button_text = await locator.text_content()
                            if button_text and any(word in button_text.lower() for word in 
                                                ['create repository', 'create project', 'create issue', 'submit']):
                                print(f"  ⚠️ Strategy {i} found final submit, but looking for intermediate, skipping...")
                                continue
                        
                        # Additional check for final submit
                        if is_final_submit and ' ' in target:
                            button_text = await locator.text_content()
                            if button_text:
                                if target.lower() not in button_text.lower():
                                    print(f"  ⚠️ Strategy {i} found '{button_text[:50]}', but looking for '{target}', skipping...")
                                    continue
                        
                        await locator.click(timeout=5000)
                        await self.page.wait_for_timeout(2000)
                        print(f"  ✅ Clicked using strategy {i}: '{strategy[:60]}'")
                        return True, f"Clicked using strategy {i}"
            except Exception as e:
                print(f"  ⚠️ Strategy {i} failed: {str(e)[:50]}")
                continue
        
        # Last resort for final submit
        if is_final_submit and ' ' in target:
            words = target.split()
            if len(words) == 2:
                print(f"  🔍 Last resort: Looking for button containing both '{words[0]}' and '{words[1]}'")
                try:
                    all_buttons = await self.page.locator("button, [role='button']").all()
                    for btn in all_buttons:
                        try:
                            btn_text = await btn.text_content()
                            if btn_text and all(word.lower() in btn_text.lower() for word in words):
                                print(f"  Found candidate: '{btn_text[:50]}'")
                                is_disabled = await btn.evaluate("el => el.disabled || el.getAttribute('aria-disabled') === 'true'")
                                if not is_disabled and await btn.is_visible():
                                    await btn.scroll_into_view_if_needed()
                                    await self.page.wait_for_timeout(500)
                                    await btn.click(timeout=5000)
                                    await self.page.wait_for_timeout(2000)
                                    print(f"  ✅ Clicked using last resort method")
                                    return True, "Clicked using last resort method"
                        except:
                            continue
                except Exception as e:
                    print(f"  Last resort failed: {str(e)[:50]}")
        
        return False, f"Could not click: {target}"
    
    async def _smart_type(self, target: str, value: str, selector_type: str = 'placeholder', target_y: int = 0) -> Tuple[bool, str]:
        """Smart typing with position awareness"""
        
        strategies = []
        
        if target and not ' ' in target and len(target) > 5:
            strategies.append(f"#{target}")
            strategies.append(f"input#{target}")
            strategies.append(f"[id='{target}']")
        
        if target:
            strategies.append(f"[name='{target}']")
            strategies.append(f"[name*='{target}' i]")
        
        if selector_type == 'placeholder' or target:
            strategies.extend([
                f"[placeholder='{target}' i]",
                f"[placeholder*='{target}' i]",
                f"[aria-placeholder*='{target}' i]"
            ])
        
        if selector_type == 'aria_label' or target:
            strategies.extend([
                f"[aria-label='{target}' i]",
                f"[aria-label*='{target}' i]"
            ])
        
        if target:
            strategies.append(f"[data-testid*='{target}' i]")
        
        if target and len(target.split()) <= 3:
            strategies.append(f"label:has-text('{target}') >> input")
            strategies.append(f":text('{target}') >> .. >> input")
        
        if target_y > 0:
            strategies.extend([
                "input[type='text']:visible",
                "input:not([type='hidden']):not([type='submit']):visible",
                "textarea:visible"
            ])
        
        strategies.append("[contenteditable='true']")
        
        best_match = None
        best_distance = float('inf')
        best_strategy_used = 0
        
        print(f"  🔍 Trying {len(strategies)} strategies to find field...")
        
        for i, selector in enumerate(strategies, 1):
            try:
                locator = self.page.locator(selector)
                count = await locator.count()
                
                if count > 0:
                    print(f"    Strategy {i}: Found {count} elements with '{selector[:50]}'")
                    
                    if target_y > 0:
                        for idx in range(min(count, 5)):
                            try:
                                elem = locator.nth(idx)
                                if await elem.is_visible(timeout=1000):
                                    bbox = await elem.bounding_box()
                                    if bbox:
                                        elem_y = int(bbox['y'])
                                        distance = abs(elem_y - target_y)
                                        
                                        max_distance = 150 if i > 6 else 80
                                        
                                        if distance < max_distance and distance < best_distance:
                                            best_distance = distance
                                            best_match = elem
                                            best_strategy_used = i
                                            print(f"      → Candidate at Y:{elem_y}, distance:{distance}px")
                                            
                                            if distance < 20:
                                                print(f"      ✓ Perfect match!")
                                                break
                            except:
                                continue
                        
                        if best_match and best_strategy_used <= 6 and best_distance < 50:
                            break
                    else:
                        for idx in range(min(count, 2)):
                            try:
                                elem = locator.nth(idx)
                                if await elem.is_visible(timeout=1000):
                                    best_match = elem
                                    best_strategy_used = i
                                    print(f"      → Using first visible element")
                                    break
                            except:
                                continue
                        
                        if best_match and i <= 6:
                            break
            except:
                continue
        
        if best_match:
            try:
                print(f"  📝 Using strategy {best_strategy_used}, Y-distance: {best_distance:.0f}px")
                
                try:
                    await best_match.click(timeout=2000, force=True)
                except:
                    await best_match.scroll_into_view_if_needed()
                    await self.page.wait_for_timeout(300)
                    await best_match.click(timeout=2000)
                
                await self.page.wait_for_timeout(500)
                
                success = False
                
                try:
                    await best_match.fill('', timeout=1000)
                    await self.page.wait_for_timeout(200)
                    await best_match.fill(value, timeout=2000)
                    await self.page.wait_for_timeout(500)
                    print(f"  ✅ Typed using fill() method")
                    success = True
                except Exception as e:
                    print(f"    ⚠️ fill() failed: {str(e)[:50]}")
                
                if not success:
                    try:
                        await best_match.click(force=True)
                        await self.page.keyboard.press('Control+A')
                        await self.page.wait_for_timeout(100)
                        await self.page.keyboard.type(value, delay=50)
                        await self.page.wait_for_timeout(500)
                        print(f"  ✅ Typed using keyboard method")
                        success = True
                    except Exception as e:
                        print(f"    ⚠️ keyboard typing failed: {str(e)[:50]}")
                
                if not success:
                    try:
                        await best_match.press_sequentially(value, delay=50)
                        await self.page.wait_for_timeout(500)
                        print(f"  ✅ Typed using press_sequentially()")
                        success = True
                    except Exception as e:
                        print(f"    ⚠️ press_sequentially failed: {str(e)[:50]}")
                
                if success:
                    return True, f"Typed using strategy {best_strategy_used}"
                else:
                    return False, "All typing methods failed"
                    
            except Exception as e:
                print(f"  ❌ Typing failed: {e}")
                return False, f"Typing error: {str(e)[:100]}"
        
        print(f"  ❌ No suitable input field found")
        return False, f"Could not find field: {target}"
    
    async def execute_task(
        self, 
        task: str, 
        app_url: str, 
        app_name: str,
        max_steps: int = 20
    ) -> TaskWorkflow:
        """Main execution loop"""
        print(f"\n{'='*60}")
        print(f"Starting Task Execution")
        print(f"Task: {task}")
        print(f"App: {app_name}")
        print(f"{'='*60}\n")
        
        try:
            print(f"🌐 Navigating to {app_url}...")
            await self.page.goto(app_url, wait_until='domcontentloaded', timeout=60000)
            await self.page.wait_for_timeout(3000)
        except:
            print(f"⚠️ Using current page: {self.page.url}")
        
        if any(kw in self.page.url.lower() for kw in ['login', 'signin', 'auth', 'sessions/verified']):
            if not await self.wait_for_login(app_name):
                raise Exception("Login failed or timeout")
        
        workflow = TaskWorkflow(
            task_description=task,
            app_name=app_name,
            app_url=app_url,
            states=[],
            total_steps=0,
            completion_status="in_progress"
        )
        
        actions_taken = []
        consecutive_failures = 0
        same_action_count = 0
        last_action_signature = ""
        initial_url = self.page.url
        creation_completed = False  # NEW: Track if creation action completed
        
        for step in range(1, max_steps + 1):
            print(f"\n{'─'*60}")
            print(f"📍 STEP {step}/{max_steps}")
            print(f"{'─'*60}")
            
            screenshot_path = await self.capture_screenshot(step, f"step_{step}")
            print(f"📸 Screenshot: {screenshot_path}")
            
            dom_data = await self.get_comprehensive_dom_context()
            print(f"DOM: {len(dom_data['elements'])} elements")
            print(f"Section: {dom_data.get('currentSection', 'unknown')}")
            if dom_data.get('isGitHub'):
                print(f"GitHub Mode")
            elif dom_data.get('isLinear'):
                print(f"Linear Mode")
            
            # NEW: Check if creation was completed by detecting URL change after final submit
            current_url = self.page.url
            if creation_completed:
                # Wait a bit for page to load after creation
                await self.page.wait_for_timeout(2000)
                
                # Check if URL changed to show the created entity
                task_lower = task.lower()
                url_indicates_success = False
                
                if 'project' in task_lower:
                    # For projects, URL should change from /projects/new to /users/{user}/projects/{number}
                    url_indicates_success = '/projects/' in current_url and '/new' not in current_url and current_url != initial_url
                elif 'repository' in task_lower or 'repo' in task_lower:
                    # For repos, URL should be /{user}/{repo-name}
                    url_indicates_success = current_url.count('/') >= 4 and '/new' not in current_url
                elif 'issue' in task_lower:
                    # For issues, URL should be /{user}/{repo}/issues/{number}
                    url_indicates_success = '/issues/' in current_url and current_url.count('/') >= 5
                
                if url_indicates_success:
                    print(f"✅ URL changed to created entity: {current_url}")
                    print(f"✅ Creation detected - Task completed!")
                    workflow.completion_status = "completed"
                    workflow.total_steps = step
                    print(f"\n{'='*60}")
                    print(f"✅ TASK COMPLETED IN {step} STEPS!")
                    print(f"{'='*60}")
                    break
                else:
                    print(f"⚠️ URL unchanged or unexpected: {current_url}")
            
            action_plan = await self.analyze_and_plan(
                task=task,
                app_name=app_name,
                current_step=step,
                dom_data=dom_data,
                previous_actions=actions_taken,
                consecutive_failures=consecutive_failures
            )
            
            print(f"Reasoning: {action_plan.get('reasoning', 'N/A')[:250]}")
            print(f"Action: {action_plan['action']}")
            if action_plan.get('target'):
                print(f"🎯 Target: {action_plan['target'][:100]}")
            
            action_signature = f"{action_plan['action']}|{action_plan.get('target', '')}|{action_plan.get('value', '')}"
            if action_signature == last_action_signature:
                same_action_count += 1
                print(f"  ⚠️ Repeated action detected ({same_action_count}x)")
                
                if same_action_count >= 3:
                    print(f"  🛑 BREAKING: Same action repeated 3 times!")
                    workflow.completion_status = "failed_infinite_loop"
                    workflow.total_steps = step
                    break
            else:
                same_action_count = 0
            
            last_action_signature = action_signature
            
            ui_state = UIState(
                step_number=step,
                description=action_plan.get('reasoning', 'Unknown'),
                screenshot_path=screenshot_path,
                url=self.page.url,
                timestamp=datetime.now().isoformat(),
                actions_taken=actions_taken.copy()
            )
            workflow.states.append(ui_state)
            
            success, message = await self.execute_action(action_plan)
            
            # NEW: Detect if this was a final submit action
            target = action_plan.get('target', '').lower()
            is_final_submit = any(phrase in target for phrase in 
                                ['create repository', 'create project', 'create issue', 
                                'submit new issue', 'create pull request', 'publish'])
            
            if success and is_final_submit:
                print(f"  🎉 Final submit action completed!")
                creation_completed = True
            
            action_summary = f"Step {step}: {action_plan['action']}"
            if action_plan.get('target'):
                action_summary += f" - {action_plan['target'][:50]}"
            action_summary += f" -> {'✓' if success else '✗'}"
            actions_taken.append(action_summary)
            
            if success:
                print(f"✅ {message}")
                consecutive_failures = 0
            else:
                print(f"❌ {message}")
                consecutive_failures += 1
            
            wait_time = action_plan.get('wait_after', 1500)
            await self.page.wait_for_timeout(wait_time)
            
            if action_plan.get('is_complete') and action_plan.get('confidence', 0) > 0.7:
                workflow.completion_status = "completed"
                workflow.total_steps = step
                print(f"\n{'='*60}")
                print(f"✅ TASK COMPLETED IN {step} STEPS!")
                print(f"{'='*60}")
                break
            
            if consecutive_failures >= 4:
                print(f"\n⚠️ Too many failures ({consecutive_failures})")
                workflow.completion_status = "failed_too_many_errors"
                workflow.total_steps = step
                break
        
        if workflow.completion_status == "in_progress":
            workflow.completion_status = "max_steps_reached"
            workflow.total_steps = max_steps
        
        return workflow
    
    def save_workflow(self, workflow: TaskWorkflow, output_path: str):
        """Save workflow to JSON"""
        with open(output_path, 'w') as f:
            json.dump(asdict(workflow), f, indent=2)
        print(f"💾 Workflow saved: {output_path}")


async def main():
    """Main entry point"""
    print("\n" + "="*60)
    print("AI multi-agent system")

    print("="*60 + "\n")
    
    agent = None
    
    try:
        agent = UniversalUIAgent()
        await agent.initialize_browser(headless=False)
        
        print("Enter task details:")
        print("="*60)
        task = input("Task (e.g., 'Create a new repository named my-test-repo'): ").strip()
        app_name = input("App (linear/github): ").strip().lower()
        
        if not task:
            print("❌ Task cannot be empty")
            return
        
        if app_name not in APP_URLS:
            print(f"❌ Unknown app. Available: {', '.join(APP_URLS.keys())}")
            return
        
        print("\nStarting task execution...")
        
        workflow = await agent.execute_task(
            task=task,
            app_url=APP_URLS[app_name],
            app_name=app_name
        )
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"workflow_{app_name}_{timestamp}.json"
        agent.save_workflow(workflow, filename)
        
        print(f"\n{'='*60}")
        print("EXECUTION SUMMARY")
        print(f"{'='*60}")
        print(f"Task: {workflow.task_description}")
        print(f"App: {workflow.app_name}")
        print(f"Status: {workflow.completion_status}")
        print(f"Steps: {workflow.total_steps}")
        print(f"Fields filled: {len(agent.field_tracker.filled_fields)}")
        print(f"{'='*60}\n")
    
    except KeyboardInterrupt:
        print("\n⚠️ Task cancelled by user")
    except Exception as e:
        print(f"\n❌ Error occurred: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if agent:
            await agent.close_browser()
            print("\n👋 Browser closed. Goodbye!")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())