const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let options, requests;
const document = {title: ''};
vm.runInNewContext(fs.readFileSync('src/client/public/static/clan/panel.js', 'utf8'), {
    Vue: function(config) { options = config; }, document, csrf_token: 'test',
    axios: {post(url, payload, config) {
        assert.equal(config.timeout, 15000);
        return new Promise((resolve, reject) => requests[payload.action] = {resolve, reject});
    }},
});
const response = {data: {code: 0, groupData: {group_name: 'Test guild', cycle: 7},
    bossData: {1: {health: 500}}, selfData: {is_admin: true, user_id: 10}}};
(async () => {
    const state = {pageReady: false, pageError: '', polls: 0, status_long_polling() {this.polls++;}};
    requests = {};
    let pending = options.methods.loadPanel.call(state);
    requests.get_data.resolve(response);
    await Promise.resolve();
    assert.equal(state.pageReady, false, 'must wait for members too');
    assert.equal(state.polls, 0);
    requests.get_member_list.resolve({data: {code: 0, members: [{qqid: 10}]}});
    await pending;
    assert.equal(state.pageReady, true);
    assert.equal(state.members[0].qqid, 10);
    assert.equal(state.polls, 1);
    assert.ok(document.title.includes('Test guild'));

    for (const action of ['get_data', 'get_member_list']) {
        requests = {};
        pending = options.methods.loadPanel.call(state);
        requests.get_data.resolve(action === 'get_data' ? {data: {code: 1, message: 'denied'}} : response);
        requests.get_member_list.resolve(action === 'get_member_list' ? {data: {code: 1, message: 'denied'}} : {data: {code: 0, members: []}});
        await pending;
        assert.equal(state.pageReady, false);
        assert.equal(state.pageError, 'denied');
        assert.equal(state.polls, 1, 'no polling after initial failure');
    }
    requests = {};
    pending = options.methods.loadPanel.call(state);
    requests.get_data.reject(new Error('offline'));
    requests.get_member_list.resolve({data: {code: 0, members: []}});
    await pending;
    assert.equal(state.pageReady, false);
    assert.ok(state.pageError.length > 0);

    requests = {};
    pending = options.methods.loadPanel.call(state);
    assert.equal(state.pageError, '', 'retry clears previous failure');
    requests.get_data.resolve(response);
    requests.get_member_list.resolve({data: {code: 0, members: []}});
    await pending;
    assert.equal(state.pageReady, true);
    assert.equal(state.polls, 2);
    console.log('Panel loading: waits for both APIs, handles failures, and retries successfully.');
})().catch(error => {console.error(error); process.exitCode = 1;});
