var vm = new Vue({
    el: '#app',
    delimiters: ['[[', ']]'],
    data: {
        ready: false, loading: false, saving: false, dirty: false, error: '',
        groupName: '', requestedBattle: null, battleId: null, canEdit: false,
        revision: '', saved: false, config: {threshold: 4000000, stages: [], overrides: {}},
        records: [], originalRanking: [], search: '', page: 1,
    },
    mounted() { this.load(false); },
    computed: {
        validationError() {
            if (!Number.isSafeInteger(this.config.threshold) || this.config.threshold < 0) return '伤害阈值必须是非负整数';
            let previous = 0;
            for (const stage of this.config.stages) {
                if (!Number.isInteger(stage.from) || !Number.isInteger(stage.to) || stage.from !== previous + 1 || stage.to < stage.from) return '阶段须从第 1 周开始，周目连续且不重叠';
                if (!this.validWeight(stage.weight, 1)) return '阶段权重须在 0 至 100 之间，最多 1 位小数';
                previous = stage.to;
            }
            if (this.records.some(row => row.cycle > previous)) return '阶段范围没有覆盖全部报刀';
            if (this.records.some(row => row.override != null && !this.validWeight(row.override))) return '单刀权重须在 0 至 100 之间，最多 1 位小数';
            return '';
        },
        stageLabels() { return Array.from({length: Math.max(3, this.config.stages.length)}, (_, index) => this.stageLabel(index)); },
        ranking() {
            if (this.validationError) return [];
            const members = {};
            for (const original of this.originalRanking) members[original.qqid] = Object.assign({}, original, {score: 0, base_score: 0, full_blade: 0, end_blade: 0, small_end_blade: 0, total_blades: 0, stage_blades: this.stageLabels.map(() => 0), stage_scores: this.stageLabels.map(() => 0)});
            for (const row of this.records) {
                const target = members[row.credited_to];
                const stageIndex = this.config.stages.findIndex(stage => row.cycle >= stage.from && row.cycle <= stage.to);
                target.total_blades++;
                target.stage_blades[stageIndex]++;
                target.stage_scores[stageIndex] += this.recordScore(row);
                target.score += this.recordScore(row);
                target.base_score += this.basePoints(row);
                const key = row.kind === '整刀' ? 'full_blade' : row.kind === '尾刀' ? 'end_blade' : 'small_end_blade';
                target[key]++;
            }
            return Object.values(members).map(row => Object.assign(row, {score: Math.round(row.score * 100000) / 100000, stage_scores: row.stage_scores.map(score => Math.round(score * 100000) / 100000)})).sort((a, b) => b.score - a.score || a.qqid - b.qqid);
        },
        filteredRecords() {
            const query = this.search.trim().toLowerCase();
            return this.records.filter(row => !query || [row.cid, row.nickname, row.credited_name, row.qqid, row.credited_to].some(value => String(value).toLowerCase().includes(query)));
        },
        visibleRecords() { return this.filteredRecords.slice((this.page - 1) * 50, this.page * 50); },
    },
    watch: { search() { this.page = 1; } },
    methods: {
        stageLabel(index) { return index < 25 ? String.fromCharCode(66 + index) : '阶段 ' + (index + 1); },
        validWeight(value, precision = 1) { return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100 && Math.abs(value * Math.pow(10, precision) - Math.round(value * Math.pow(10, precision))) < 1e-7; },
        changed() { this.dirty = true; },
        basePoints(row) { return row.kind === '整刀' || row.damage >= this.config.threshold ? 1 : 0.5; },
        effectiveWeight(row) {
            if (row.override != null) return row.override;
            const stage = this.config.stages.find(stage => row.cycle >= stage.from && row.cycle <= stage.to);
            return stage ? stage.weight : null;
        },
        recordScore(row) { const value = this.effectiveWeight(row); return value == null ? 0 : this.basePoints(row) * value; },
        number(value) { return Number(value).toLocaleString('zh-CN', {maximumFractionDigits: 5}); },
        pcrDate(day) { return new Date(day * 86400000).toISOString().slice(0, 10); },
        overrideChanged(row) { if (!Number.isFinite(row.override)) row.override = null; this.changed(); },
        setOverride(row) { row.override = this.effectiveWeight(row); this.changed(); },
        clearOverride(row) { row.override = null; this.changed(); },
        addStage() {
            const last = this.config.stages[this.config.stages.length - 1];
            this.config.stages.push({from: last.to + 1, to: last.to + 100, weight: 1}); this.changed();
        },
        removeStage() { this.config.stages.pop(); this.changed(); },
        resetWeights() { for (const stage of this.config.stages) stage.weight = 1; for (const row of this.records) row.override = null; this.changed(); },
        apply(data) {
            this.config = data.config;
            for (const stage of this.config.stages) stage.weight = Number(stage.weight);
            this.records = data.records.map(row => Object.assign(row, {override: row.override == null ? null : Number(row.override)}));
            this.originalRanking = data.ranking; this.revision = data.revision; this.saved = data.saved;
            this.canEdit = data.can_edit; this.battleId = data.battle_id; this.requestedBattle = data.battle_id;
            this.groupName = data.group_name; this.dirty = false; this.ready = true; this.page = 1;
            document.title = this.groupName + ' · 业绩表';
        },
        async load(confirmChanges) {
            if (confirmChanges && this.dirty) {
                try { await this.$confirm('刷新或切换档案会放弃未保存的权重，是否继续？', '未保存的修改'); } catch (_) { return; }
            }
            this.loading = true; this.error = '';
            try {
                const params = this.requestedBattle == null ? {} : {battle_id: this.requestedBattle};
                const response = await axios.get('./api/', {params});
                if (response.data.code !== 0) throw new Error(response.data.message);
                this.apply(response.data);
            } catch (error) { this.error = error.message || String(error); }
            finally { this.loading = false; }
        },
        async save() {
            if (this.validationError) { this.error = this.validationError; return; }
            this.saving = true; this.error = '';
            try {
                const overrides = {};
                for (const row of this.records) if (row.override != null) overrides[row.cid] = String(row.override);
                const config = {threshold: this.config.threshold, stages: this.config.stages.map(stage => ({from: stage.from, to: stage.to, weight: String(stage.weight)})), overrides};
                const response = await axios.put('./api/', {battle_id: this.battleId, config, revision: this.revision, csrf_token: performanceCsrf});
                if (response.data.code !== 0) throw new Error(response.data.message);
                this.apply(response.data); this.$message.success('权重已保存，群内业绩表同步生效');
            } catch (error) { this.error = error.message || String(error); }
            finally { this.saving = false; }
        },
        exportCsv() {
            const cell = value => '"' + String(value).replace(/^[=+@-]/, "'$&").replace(/"/g, '""') + '"';
            const rows = [['QQ', '成员', '总刀数', ...this.stageLabels.map(label => label + '阶段'), ...this.stageLabels.map(label => label + '得分'), '总业绩分'], ...this.ranking.map(row => [row.qqid, row.nickname, row.total_blades, ...row.stage_blades, ...row.stage_scores, row.score])];
            const url = URL.createObjectURL(new Blob(['\ufeff' + rows.map(row => row.map(cell).join(',')).join('\r\n')], {type: 'text/csv;charset=utf-8'}));
            const link = document.createElement('a'); link.href = url; link.download = '业绩表-档案' + this.battleId + (this.dirty ? '-预览' : '') + '.csv'; link.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        },
    },
});
