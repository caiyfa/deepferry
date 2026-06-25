const { createApp, reactive, ref, computed, onMounted, onUnmounted } = Vue;

const API = '';

const TABS = [
    {
        key: 'employees', label: '员工', endpoint: '/api/v1/employees',
        icon: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="8" r="3"/><path d="M3 20v-1a6 6 0 0 1 12 0v1"/><path d="M16 11a3 3 0 0 0 0-6"/><path d="M21 20v-1a6 6 0 0 0-4-5.6"/></svg>',
        filters: [
            { key: 'department', type: 'text', placeholder: '按部门筛选（如 销售部）' },
        ],
        columns: [
            { path: 'id', label: 'ID' },
            { path: 'empNo', label: '工号' },
            { path: 'name', label: '姓名' },
            { path: 'department', label: '部门' },
            { path: 'position', label: '职位' },
            { path: 'email', label: '邮箱' },
            { path: 'hireDate', label: '入职日期' },
        ],
    },
    {
        key: 'invoices', label: '发票', endpoint: '/api/v1/invoices',
        icon: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 2h9l5 5v15a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z"/><path d="M15 2v5h5"/><path d="M9 13h6M9 17h6M9 9h3"/></svg>',
        filters: [
            { key: 'invoiceType', type: 'select', placeholder: '全部类型',
              options: ['专票', '普票'] },
        ],
        columns: [
            { path: 'id', label: 'ID' },
            { path: 'invoiceNo', label: '发票号' },
            { path: 'invoiceCode', label: '发票代码' },
            { path: 'invoiceType', label: '类型' },
            { path: 'seller.name', label: '销方名称' },
            { path: 'seller.taxNo', label: '销方税号' },
            { path: 'seller.address', label: '销方地址' },
            { path: 'seller.phone', label: '销方电话' },
            { path: 'buyer.name', label: '购方名称' },
            { path: 'buyer.taxNo', label: '购方税号' },
            { path: 'amount', label: '不含税额', numeric: true },
            { path: 'taxRate', label: '税率' },
            { path: 'taxAmount', label: '税额', numeric: true },
            { path: 'totalAmount', label: '价税合计', numeric: true },
            { path: 'issueDate', label: '开票日期' },
        ],
    },
    {
        key: 'reimbursements', label: '报销', endpoint: '/api/v1/reimbursements',
        icon: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M8 14h5"/></svg>',
        filters: [
            { key: 'status', type: 'select', placeholder: '全部状态',
              options: ['draft', 'pending', 'paid', 'rejected'] },
            { key: 'category', type: 'text', placeholder: '按类别筛选（如 差旅）' },
            { key: 'department', type: 'text', placeholder: '按部门筛选' },
        ],
        columns: [
            { path: 'id', label: 'ID' },
            { path: 'reimbNo', label: '报销号' },
            { path: 'employee.empNo', label: '员工工号' },
            { path: 'employee.name', label: '员工姓名' },
            { path: 'employee.department', label: '员工部门' },
            { path: 'invoice.invoiceNo', label: '关联发票号' },
            { path: 'invoice.totalAmount', label: '发票金额', numeric: true },
            { path: 'category', label: '类别' },
            { path: 'amount', label: '报销金额', numeric: true },
            { path: 'description', label: '事由' },
            { path: 'status', label: '状态', status: true },
            { path: 'applyDate', label: '申请日' },
            { path: 'approvedBy', label: '审批人' },
        ],
    },
    {
        key: 'vouchers', label: '凭证', endpoint: '/api/v1/vouchers',
        icon: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16v16H4z"/><path d="M4 9h16M9 4v16M14 13h4M14 16h4"/></svg>',
        filters: [
            { key: 'status', type: 'select', placeholder: '全部状态',
              options: ['draft', 'posted', 'audited', 'unaudited'] },
            { key: 'period', type: 'text', placeholder: '按期间筛选（如 2026-06）' },
        ],
        columns: [
            { path: 'id', label: 'ID' },
            { path: 'voucherNo', label: '凭证号' },
            { path: 'period', label: '期间' },
            { path: 'reimb.reimbNo', label: '关联报销号' },
            { path: 'reimb.amount', label: '报销金额', numeric: true },
            { path: 'reimb.employeeName', label: '报销人' },
            { path: 'summary', label: '摘要' },
            { path: 'totalDebit', label: '借方合计', numeric: true },
            { path: 'totalCredit', label: '贷方合计', numeric: true },
            { path: 'postedBy', label: '制单' },
            { path: 'postedDate', label: '过账日' },
            { path: 'status', label: '状态', status: true },
        ],
    },
];

const STATUS_MAP = {
    paid: 's-paid', posted: 's-posted', audited: 's-audited',
    pending: 's-pending', draft: 's-draft',
    rejected: 's-rejected', unaudited: 's-unaudited',
};

createApp({
    setup() {
        const auth = reactive({
            username: 'admin', password: 'deepferry123',
            token: localStorage.getItem('df_token') || '',
            refreshToken: localStorage.getItem('df_refresh') || '',
            loading: false, error: null,
        });

        const activeTab = ref('employees');
        const rows = ref([]);
        const total = ref(0);
        const loading = ref(false);
        const dataError = ref(null);
        const filters = reactive({});
        const expandedId = ref(null);
        const tokenRemaining = ref(0);
        const toasts = ref([]);
        let countdownTimer = null;
        let toastSeq = 0;

        const currentTab = computed(() => TABS.find(t => t.key === activeTab.value));

        function pushToast(type, title, msg) {
            const id = ++toastSeq;
            toasts.value.push({ id, type, title, msg });
            setTimeout(() => {
                toasts.value = toasts.value.filter(t => t.id !== id);
            }, 4500);
        }

        function resolvePath(obj, path) {
            if (!path) return '';
            return path.split('.').reduce((acc, k) => (acc == null ? null : acc[k]), obj);
        }

        function formatNum(v) {
            if (v == null || v === '') return '—';
            const n = Number(v);
            if (Number.isNaN(n)) return v;
            return n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        }

        function formatCountdown(sec) {
            if (sec <= 0) return '已过期';
            const m = Math.floor(sec / 60);
            const s = sec % 60;
            return m > 0 ? `${m}分${String(s).padStart(2, '0')}秒` : `${s}秒`;
        }

        function statusClass(v) {
            return STATUS_MAP[v] || 's-draft';
        }

        function isBalanced(row) {
            return Number(row.totalDebit) === Number(row.totalCredit);
        }

        function toggleExpand(id) {
            expandedId.value = expandedId.value === id ? null : id;
        }

        async function login() {
            auth.loading = true;
            auth.error = null;
            try {
                const res = await fetch(`${API}/auth/login`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username: auth.username, password: auth.password }),
                });
                const data = await res.json();
                if (!res.ok) {
                    auth.error = {
                        code: data.code || 'AUTH_FAILED',
                        message: data.message || '登录失败',
                        suggestion: data.suggestion || '',
                    };
                    return;
                }
                auth.token = data.access_token;
                auth.refreshToken = data.refresh_token || '';
                localStorage.setItem('df_token', auth.token);
                if (auth.refreshToken) localStorage.setItem('df_refresh', auth.refreshToken);
                tokenRemaining.value = data.expires_in || 0;
                localStorage.setItem('df_exp', String(Date.now() + tokenRemaining.value * 1000));
                startCountdown();
                pushToast('success', '登录成功', `欢迎回来，${auth.username}`);
                switchTab('employees');
            } catch (e) {
                auth.error = {
                    code: 'NETWORK',
                    message: '无法连接到服务，请确认后端已启动',
                    suggestion: '检查 http://localhost:8080 是否可达',
                };
            } finally {
                auth.loading = false;
            }
        }

        function logout() {
            auth.token = '';
            auth.refreshToken = '';
            localStorage.removeItem('df_token');
            localStorage.removeItem('df_refresh');
            localStorage.removeItem('df_exp');
            tokenRemaining.value = 0;
            stopCountdown();
            rows.value = [];
            total.value = 0;
            pushToast('info', '已退出登录');
        }

        function startCountdown() {
            stopCountdown();
            countdownTimer = setInterval(() => {
                if (tokenRemaining.value > 0) {
                    tokenRemaining.value--;
                    if (tokenRemaining.value === 60) {
                        pushToast('info', 'Token 即将过期', '剩余不足 1 分钟，请及时保存数据');
                    }
                    if (tokenRemaining.value === 0) {
                        pushToast('error', 'Token 已过期', '请重新登录');
                        logout();
                    }
                }
            }, 1000);
        }

        function stopCountdown() {
            if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
        }

        function switchTab(key) {
            activeTab.value = key;
            Object.keys(filters).forEach(k => delete filters[k]);
            expandedId.value = null;
            loadData(true);
        }

        function resetFilters() {
            Object.keys(filters).forEach(k => delete filters[k]);
            loadData(true);
        }

        async function loadData(showToast = false) {
            if (!auth.token) return;
            loading.value = true;
            dataError.value = null;
            const tab = currentTab.value;
            const params = new URLSearchParams();
            tab.filters.forEach(f => {
                if (filters[f.key]) params.append(f.key, filters[f.key]);
            });
            const url = `${API}${tab.endpoint}${params.toString() ? '?' + params.toString() : ''}`;
            try {
                const res = await fetch(url, {
                    headers: { 'Authorization': `Bearer ${auth.token}` },
                });
                if (res.status === 401) {
                    const data = await res.json().catch(() => ({}));
                    dataError.value = {
                        code: data.code || 'AUTH_FAILED',
                        message: data.message || 'Token 已过期或无效',
                        suggestion: data.suggestion || '请重新登录',
                    };
                    pushToast('error', '认证失败', 'Token 已过期，请重新登录');
                    logout();
                    return;
                }
                if (!res.ok) {
                    const data = await res.json().catch(() => ({}));
                    dataError.value = {
                        code: data.code || `HTTP_${res.status}`,
                        message: data.message || `请求失败 (${res.status})`,
                        suggestion: data.suggestion || '',
                    };
                    return;
                }
                const data = await res.json();
                rows.value = data.data || [];
                total.value = data.total ?? rows.value.length;
                if (showToast) pushToast('success', '查询完成', `共 ${total.value} 条${tab.label}数据`);
            } catch (e) {
                dataError.value = {
                    code: 'NETWORK',
                    message: '网络请求失败',
                    suggestion: '请确认后端服务正在运行',
                };
                pushToast('error', '网络错误', String(e.message || e));
            } finally {
                loading.value = false;
            }
        }

        onMounted(() => {
            if (auth.token) {
                const exp = localStorage.getItem('df_exp');
                if (exp) tokenRemaining.value = Math.max(0, Math.floor((Number(exp) - Date.now()) / 1000));
                if (tokenRemaining.value > 0) {
                    startCountdown();
                    switchTab('employees');
                } else {
                    logout();
                }
            }
        });

        onUnmounted(stopCountdown);

        return {
            auth, tabs: TABS, activeTab, currentTab, rows, total, loading,
            dataError, filters, expandedId, tokenRemaining, toasts,
            login, logout, switchTab, loadData, resetFilters,
            resolvePath, formatNum, formatCountdown, statusClass,
            isBalanced, toggleExpand,
        };
    },
}).mount('#app');