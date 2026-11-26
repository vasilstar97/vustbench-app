import numpy as np
import pandas as pd
import pytest

from vustbench_core.social import evaluate_support, summarize_support


def test_missing_weights_renormalize_but_zero_is_known():
    index = pd.Index(['a', 'b', 'c'])
    nodes = [
        {'id': 'school', 'operation': 'service', 'service': 'school'},
        {'id': 'missing', 'operation': 'service', 'service': 'library'},
        {'id': 'support', 'operation': 'weighted_mean', 'dependencies': [
            {'id': 'school', 'weight': .75}, {'id': 'missing', 'weight': .25}]},
    ]
    result = evaluate_support(nodes, {'school': pd.Series([0, .8, np.nan], index=index)}, index)
    assert result.values.loc['a', 'support'] == 0
    assert result.values.loc['b', 'support'] == pytest.approx(.8)
    assert pd.isna(result.values.loc['c', 'support'])
    assert result.coverage['support'].tolist() == [.75, .75, 0]
    assert result.values['missing'].isna().all()


def test_formula_requires_four_dimensions_and_city_evaluates_summed_demands():
    index = pd.Index(['a', 'b', 'unknown'])
    nodes = [{'id': s, 'operation': 'service', 'service': s} for s in ['w', 'l', 'd', 'r']]
    nodes += [{'id': 'ksd', 'operation': 'k_sd', 'dependencies': [{'id': s} for s in ['w','l','d','r']]}]
    # Both known blocks have K_SD=0, although K_SD of their mean dimensions is .25.
    inputs = {s: pd.Series(v, index=index) for s,v in {
        'w': [1,0,1], 'l': [1,0,1], 'd': [0,1,1], 'r': [0,1,np.nan]}.items()}
    result = evaluate_support(nodes, inputs, index)
    assert result.values['ksd'].iloc[:2].tolist() == [0,0]
    assert pd.isna(result.values.loc['unknown','ksd'])
    # Territory values use the sums of service demands, then formula (46).
    # Averaging the two known block K_SD values would incorrectly yield zero.
    summary = summarize_support(nodes, {s:(1000,500) for s in ['w','l','d','r']})['ksd']
    assert summary['value'] == .25
    assert summary['coverage'] == 1


def test_territory_provision_uses_demand_not_population_or_mean_of_ratios():
    nodes = [{'id':'school','operation':'service','service':'school'}]
    # A block with demand=1 is fully supplied, another with demand=9 is not.
    assert summarize_support(nodes, {'school':(1+9,1+0)})['school']['value'] == .1
    assert summarize_support(nodes, {'school':(0,0)})['school']['value'] is None
    assert summarize_support(nodes, {})['school']['value'] is None


def test_support_best_case_and_missing_work():
    index = pd.Index(['a'])
    nodes = [{'id': s, 'operation': 'service', 'service': s} for s in ['w','l','d','r']]
    nodes += [{'id':'ksd','operation':'k_sd','dependencies':[{'id':s} for s in ['w','l','d','r']]}]
    inputs = {s: pd.Series([1.0],index=index) for s in ['w','l','d','r']}
    assert evaluate_support(nodes,inputs,index).values.loc['a','ksd'] == 1
    nodes[0] = {'id':'w','operation':'unavailable'}
    assert pd.isna(evaluate_support(nodes,inputs,index).values.loc['a','ksd'])


@pytest.mark.parametrize('weight',[0,-1,float('inf')])
def test_invalid_weights_rejected(weight):
    nodes=[{'id':'x','operation':'unavailable'},
           {'id':'y','operation':'weighted_mean','dependencies':[{'id':'x','weight':weight}]}]
    with pytest.raises(ValueError,match='weights'):
        evaluate_support(nodes,{},pd.Index(['a']))


def test_cycles_rejected():
    nodes=[{'id':'x','operation':'weighted_mean','dependencies':[{'id':'x'}]}]
    with pytest.raises(ValueError,match='Cyclic'):
        evaluate_support(nodes,{},pd.Index(['a']))
