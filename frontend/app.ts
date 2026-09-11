// Frontend TypeScript for Automação Acadêmica IA
// This file provides interactivity to the HTML interface

interface Step {
    id: number;
    label: string;
    completed: boolean;
    active: boolean;
}

interface AppState {
    sidebarOpen: boolean;
    activeStep: number;
    uploading: boolean;
    searching: boolean;
    downloading: boolean;
    analyzing: boolean;
    showQueryEditor: boolean;
    showSettings: boolean;
    showReport: boolean;
    selectedFile: File | null;
    status: {
        steps: Step[];
    };
    steps: {
        id: number;
        label: string;
        number: string;
    }[];
}

class App {
    private state: AppState;

    constructor() {
        this.state = {
            sidebarOpen: false,
            activeStep: 0,
            uploading: false,
            searching: false,
            downloading: false,
            analyzing: false,
            showQueryEditor: false,
            showSettings: false,
            showReport: false,
            selectedFile: null,
            status: {
                steps: [
                    { id: 0, label: 'Upload', completed: false, active: false },
                    { id: 1, label: 'Busca', completed: false, active: false },
                    { id: 2, label: 'Download', completed: false, active: false },
                    { id: 3, label: 'Análise IA', completed: false, active: false },
                    { id: 4, label: 'Relatório', completed: false, active: false },
                    { id: 5, label: 'Configurações', completed: false, active: false }
                ]
            },
            steps: [
                { id: 0, label: 'Upload', number: '01' },
                { id: 1, label: 'Busca', number: '02' },
                { id: 2, label: 'Download', number: '03' },
                { id: 3, label: 'Análise IA', number: '04' },
                { id: 4, label: 'Relatório', number: '05' },
                { id: 5, label: 'Configurações', number: '06' }
            ]
        };

        this.initializeAlpine();
        this.setupEventListeners();
        this.loadInitialState();
    }

    private initializeAlpine() {
        // Alpine.js is initialized via CDN in HTML
        // We'll expose our app to window for Alpine to access
        (window as any).app = this.state;
    }

    private setupEventListeners() {
        // Handle file upload
        const fileInput = document.getElementById('fileUpload');
        if (fileInput) {
            fileInput.addEventListener('change', (e) => {
                const target = e.target as HTMLInputElement;
                if (target.files && target.files[0]) {
                    this.state.selectedFile = target.files[0];
                    // Update the UI to show selected file
                    const selectedFileInfo = document.querySelector('[x-show="selectedFile"]');
                    if (selectedFileInfo) {
                        selectedFileInfo.setAttribute('x-show', 'true');
                        // In a real implementation, we'd update the filename display
                    }
                }
            });
        }

        // Handle window resize for responsive sidebar
        window.addEventListener('resize', () => {
            this.updateResponsiveClasses();
        });

        // Handle keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowRight' && this.state.activeStep < 5) {
                e.preventDefault();
                this.nextStep();
            } else if (e.key === 'ArrowLeft' && this.state.activeStep > 0) {
                e.preventDefault();
                this.previousStep();
            }
        });
    }

    private updateResponsiveClasses() {
        const isMd = window.innerWidth >= 768;
        const sidebar = document.querySelector('nav');
        const mainContent = document.querySelector('main');

        if (sidebar && mainContent) {
            if (isMd) {
                sidebar.classList.remove('-translate-x-full');
                sidebar.classList.add('translate-x-0');
                mainContent.classList.remove('ml-64');
                mainContent.classList.add('ml-0');
            } else {
                if (!this.state.sidebarOpen) {
                    sidebar.classList.add('-translate-x-full');
                    sidebar.classList.remove('translate-x-0');
                    mainContent.classList.add('ml-64');
                    mainContent.classList.remove('ml-0');
                }
            }
        }
    }

    private loadInitialState() {
        // In a real app, we would load from localStorage or API
        // For now, we'll just set up initial responsive state
        this.updateResponsiveClasses();
    }

    // Navigation methods
    nextStep() {
        if (this.state.activeStep < 5) {
            this.state.activeStep++;
            this.updateStepStatus();
        }
    }

    previousStep() {
        if (this.state.activeStep > 0) {
            this.state.activeStep--;
            this.updateStepStatus();
        }
    }

    private updateStepStatus() {
        // Reset all steps
        this.state.status.steps.forEach((step, index) => {
            step.completed = index < this.state.activeStep;
            step.active = index === this.state.activeStep;
        });

        // Activate next step if current is completed
        if (this.state.activeStep < 5 && this.state.status.steps[this.state.activeStep].completed) {
            this.state.status.steps[this.state.activeStep + 1].active = true;
        }
    }

    // Action methods
    uploadFile() {
        const fileInput = document.getElementById('fileUpload') as HTMLInputElement;
        if (fileInput.files && fileInput.files[0]) {
            this.state.selectedFile = fileInput.files[0];
            this.state.uploading = true;

            // Simulate upload process
            setTimeout(() => {
                this.state.uploading = false;
                // Mark upload as complete
                this.state.status.steps[0].completed = true;
                this.state.status.steps[0].active = true;
                this.state.status.steps[1].active = true;
                this.state.activeStep = 1;
                this.updateStepStatus();
            }, 2000);
        }
    }

    openQueryEditor() {
        this.state.showQueryEditor = true;
    }

    runSearch() {
        this.state.searching = true;
        setTimeout(() => {
            this.state.searching = false;
            this.state.status.steps[1].completed = true;
            this.state.status.steps[1].active = false;
            this.state.status.steps[2].active = true;
            this.state.activeStep = 2;
            this.updateStepStatus();
        }, 3000);
    }

    startDownload() {
        this.state.downloading = true;
        setTimeout(() => {
            this.state.downloading = false;
            this.state.status.steps[2].completed = true;
            this.state.status.steps[2].active = false;
            this.state.status.steps[3].active = true;
            this.state.activeStep = 3;
            this.updateStepStatus();
        }, 4000);
    }

    startAnalysis() {
        this.state.analyzing = true;
        setTimeout(() => {
            this.state.analyzing = false;
            this.state.status.steps[3].completed = true;
            this.state.status.steps[3].active = false;
            this.state.status.steps[4].active = true;
            this.state.activeStep = 4;
            this.updateStepStatus();
        }, 5000);
    }

    viewReport() {
        this.state.showReport = true;
        // In a real app, we would fetch the report from the API
        setTimeout(() => {
            const reportContent = document.getElementById('reportContent');
            if (reportContent) {
                reportContent.innerHTML = `
                    <h3 class="text-xl font-bold text-gray-900 mb-4">Relatório de Revisão Sistemática</h3>
                    <p class="text-gray-700 mb-6">Este é um relatório de exemplo mostrando os resultados da revisão sistemática realizada pelo sistema.</p>
                    <div class="space-y-4">
                        <div class="border-t pt-4">
                            <h4 class="text-lg font-semibold text-gray-900 mb-2">Resumo Executivo</h4>
                            <p class="text-gray-600">Foram encontrados 15 artigos relevantes após a aplicação dos critérios de inclusão e exclusão. Destes, 8 foram incluídos na revisão final, 4 foram excluídos e 3 requerem revisão manual.</p>
                        </div>
                        <div class="border-t pt-4">
                            <h4 class="text-lg font-semibold text-gray-900 mb-2">Fluxo PRISMA</h4>
                            <p class="text-gray-600">O fluxo PRISMA mostra o processo de seleção dos artigos através das diferentes etapas da revisão sistemática.</p>
                        </div>
                    </div>
                `;
            }
        }, 1000);
    }

    saveSettings() {
        this.state.showSettings = false;
        alert('Configurações salvas com sucesso!');
    }
}

// Initialize the app when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new App();
});

// Expose methods to window for use in HTML inline event handlers
(window as any).nextStep = () => {
    const app = (window as any).appInstance || new App();
    (window as any).appInstance = app;
    app.nextStep();
};

(window as any).previousStep = () => {
    const app = (window as any).appInstance || new App();
    (window as any).appInstance = app;
    app.previousStep();
};

(window as any).uploadFile = () => {
    const app = (window as any).appInstance || new App();
    (window as any).appInstance = app;
    app.uploadFile();
};

(window as any).openQueryEditor = () => {
    const app = (window as any).appInstance || new App();
    (window as any).appInstance = app;
    app.openQueryEditor();
};

(window as any).runSearch = () => {
    const app = (window as any).appInstance || new App();
    (window as any).appInstance = app;
    app.runSearch();
};

(window as any).startDownload = () => {
    const app = (window as any).appInstance || new App();
    (window as any).appInstance = app;
    app.startDownload();
};

(window as any).startAnalysis = () => {
    const app = (window as any).appInstance || new App();
    (window as any).appInstance = app;
    app.startAnalysis();
};

(window as any).viewReport = () => {
    const app = (window as any).appInstance || new App();
    (window as any).appInstance = app;
    app.viewReport();
};

(window as any).saveSettings = () => {
    const app = (window as any).appInstance || new App();
    (window as any).appInstance = app;
    app.saveSettings();
};