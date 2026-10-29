/* XLSX exports use row data, never the rendered Element UI table. */
(function (global) {
    'use strict';
    var libraryUrl = document.currentScript.getAttribute('data-xlsx-src');
    var libraryLoading = null;

    function library() {
        if (global.XLSX) return Promise.resolve(global.XLSX);
        if (!libraryLoading) {
            libraryLoading = new Promise(function (resolve, reject) {
                var script = document.createElement('script');
                var timer = setTimeout(function () { failed(); }, 15000);
                function failed() {
                    clearTimeout(timer);
                    script.remove();
                    libraryLoading = null;
                    reject(new Error('Excel export library could not be loaded'));
                }
                script.src = libraryUrl;
                script.onload = function () {
                    clearTimeout(timer);
                    if (global.XLSX) resolve(global.XLSX);
                    else failed();
                };
                script.onerror = failed;
                document.head.appendChild(script);
            });
        }
        return libraryLoading;
    }

    function recordText(record, findName) {
        if (!record) return '';
        var text = '(' + record.cycle + '-' + record.boss_num + ') ' + Number(record.damage).toLocaleString('zh-CN');
        if (record.health_remain === 0 && !record.is_continue) {
            text += ' · 返' + (record.return_seconds == null ? '?' : record.return_seconds) + 's';
        }
        if (record.behalf) text += ' · ' + String(findName(record.behalf)) + '代刀';
        return text;
    }

    function details(row, findName) {
        return [0, 1, 2, 3, 4, 5].map(function (index) {
            return recordText((row.detail || [])[index], findName);
        });
    }

    function workbook(XLSX, title, identityHeaders, records, identityCells, options) {
        var lead = identityHeaders.length;
        var headers = identityHeaders.concat(['第一刀', '', '第二刀', '', '第三刀', '']);
        var subheaders = identityHeaders.map(function () { return ''; }).concat(['整刀/尾刀', '补偿刀', '整刀/尾刀', '补偿刀', '整刀/尾刀', '补偿刀']);
        if (options.sl) { headers.push('SL'); subheaders.push(''); }
        var rows = [headers, subheaders].concat(records.map(function (row) {
            var values = identityCells(row).concat(details(row, options.findName));
            if (options.sl) values.push(options.today === -1 ? '—' : (row.sl == options.today ? '已用SL' : '可用'));
            return values;
        }));
        var sheet = XLSX.utils.aoa_to_sheet(rows);
        sheet['!merges'] = identityHeaders.map(function (_, index) {
            return {s: {r: 0, c: index}, e: {r: 1, c: index}};
        });
        for (var index = 0; index < 3; index++) {
            sheet['!merges'].push({s: {r: 0, c: lead + index * 2}, e: {r: 0, c: lead + index * 2 + 1}});
        }
        if (options.sl) sheet['!merges'].push({s: {r: 0, c: headers.length - 1}, e: {r: 1, c: headers.length - 1}});
        sheet['!cols'] = headers.map(function (_, column) {
            return {wch: column >= lead && column < lead + 6 ? 42 : (column === lead - 1 ? 12 : 24)};
        });
        sheet['!rows'] = rows.map(function () { return {hpt: 24}; });
        var result = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(result, sheet, title);
        return result;
    }

    function progressWorkbook(XLSX, records, options) {
        return workbook(XLSX, '出刀记录', ['QQ号', '昵称', '已出刀数'], records, function (row) {
            // QQ IDs and nicknames must remain literal strings, including leading =/+/-.
            return [String(row.qqid), String(row.nickname || ''), Number(row.finished)];
        }, Object.assign({}, options, {sl: true}));
    }

    function personalWorkbook(XLSX, records, options) {
        return workbook(XLSX, '个人出刀记录', ['日期', '已出刀数'], records, function (row) {
            return [String(row.pcrdate), Number(row.finished)];
        }, Object.assign({}, options, {sl: false}));
    }

    function download(XLSX, book, filename) {
        var bytes = XLSX.write(book, {bookType: 'xlsx', type: 'array', compression: true});
        var blob = new Blob([bytes], {type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});
        var url = URL.createObjectURL(blob);
        var anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename.replace(/[\\/:*?"<>|\x00-\x1f]/g, '_');
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 30000);
    }

    function dateLabel(value) {
        var date = value == null ? new Date() : new Date(value);
        if (isNaN(date.getTime())) date = new Date();
        return date.getFullYear() + '-' + String(date.getMonth() + 1).padStart(2, '0') + '-' + String(date.getDate()).padStart(2, '0');
    }

    global.ClanExcel = {
        progressWorkbook: progressWorkbook,
        personalWorkbook: personalWorkbook,
        progress: function (records, options) {
            return library().then(function (XLSX) {
                download(XLSX, progressWorkbook(XLSX, records, options), '出刀记录_' + dateLabel(options.date) + '.xlsx');
            });
        },
        personal: function (records, options) {
            return library().then(function (XLSX) {
                download(XLSX, personalWorkbook(XLSX, records, options), '个人出刀记录_' + String(options.nickname || '') + '.xlsx');
            });
        },
    };
})(window);
